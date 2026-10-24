"""ECS on Fargate, as far as this design needs it: task definitions with a task role, RunTask authorized
like AWS (ecs:RunTask on the task definition + iam:PassRole on the task role, which is what an EventBridge
target role must hold), and tasks that receive task-role credentials.

Fidelity note: AWS does not revoke task-role credentials when a task stops. ECS vends temporary
credentials that stay valid until they expire (rotated hourly, up to about 6 h). The PHI boundary comes from
the container being destroyed, not from the credentials dying. The sim keeps that: a stopped task's
credentials still work until expiry, and a test shows it.
"""
from __future__ import annotations

import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from .errors import AccessDenied, InvalidArgument
from .iam import ACCOUNT, REGION, Principal, decide
from .sts import STS, Session, service_principal


@dataclass
class TaskDefinition:
    family: str
    revision: int
    task_role_arn: str
    entrypoint: Callable[["TaskEnv"], Any]
    environment: dict = field(default_factory=dict)
    cpu: str = "1024"
    memory: str = "2048"

    @property
    def arn(self):
        return f"arn:aws:ecs:{REGION}:{ACCOUNT}:task-definition/{self.family}:{self.revision}"


@dataclass
class TaskEnv:
    """What the container sees: env vars, its task-role credentials, and its own ARN."""
    environment: dict
    credentials: Session
    task_arn: str
    aws: Any                                       # the LocalAWS account (to build clients against the endpoint)


@dataclass
class Task:
    task_arn: str
    task_definition_arn: str
    started_by: str | None
    environment: dict
    last_status: str = "PROVISIONING"
    exit_code: int | None = None
    stopped_reason: str | None = None
    result: Any = None
    created_at: float = 0.0
    started_at: float | None = None
    stopped_at: float | None = None
    credentials_access_key: str | None = None


class ECS:
    def __init__(self, iam, sts: STS, clock, audit, account=None):
        self.iam, self.sts, self.clock, self.audit, self.account = iam, sts, clock, audit, account
        self.task_definitions: dict[str, list[TaskDefinition]] = {}
        self.tasks: list[Task] = []

    def register_task_definition(self, family: str, taskRoleArn: str, entrypoint, environment: dict | None = None,
                                 cpu="1024", memory="2048") -> TaskDefinition:
        revs = self.task_definitions.setdefault(family, [])
        td = TaskDefinition(family, len(revs) + 1, taskRoleArn, entrypoint, dict(environment or {}), cpu, memory)
        revs.append(td)
        return td

    def _resolve(self, ref: str) -> TaskDefinition:
        name = ref.rsplit("/", 1)[-1]
        fam, _, rev = name.partition(":")
        revs = self.task_definitions.get(fam)
        if not revs:
            raise InvalidArgument(f"TaskDefinition not found: {ref}", code="ClientException")
        return revs[int(rev) - 1] if rev else revs[-1]

    def run_task(self, caller: Session, taskDefinition: str, cluster: str = "claimclarity",
                 overrides: dict | None = None, startedBy: str | None = None, launchType: str = "FARGATE") -> dict:
        td = self._resolve(taskDefinition)
        p: Principal = caller.principal()
        role = self.iam.role_by_arn(caller.role_arn)
        pol = role.policies if role else []
        for action, resource, ctx in (("ecs:RunTask", td.arn, {"ecs:cluster": f"arn:aws:ecs:{REGION}:{ACCOUNT}:cluster/{cluster}"}),
                                      ("iam:PassRole", td.task_role_arn, {"iam:PassedToService": "ecs-tasks.amazonaws.com"})):
            d = decide(p, action, resource, ctx, pol, None, caller.session_policies, role.boundary if role else None)
            if not d:
                self.audit.record("ecs.amazonaws.com" if action.startswith("ecs") else "iam.amazonaws.com", "RunTask", p,
                                  {"taskDefinition": td.arn, "cluster": cluster}, error=("AccessDenied", d.reason),
                                  resources=[resource])
                raise AccessDenied(f"{p.arn} is not authorized to perform: {action} on resource: {resource}")
        env = dict(td.environment)
        for co in (overrides or {}).get("containerOverrides", []):
            env.update({e["name"]: e["value"] for e in co.get("environment", [])})
        task_id = uuid.uuid4().hex
        t = Task(f"arn:aws:ecs:{REGION}:{ACCOUNT}:task/{cluster}/{task_id}", td.arn, startedBy, env,
                 created_at=self.clock())
        self.tasks.append(t)
        self.audit.record("ecs.amazonaws.com", "RunTask", p, {"taskDefinition": td.arn, "cluster": cluster,
                          "overrides": overrides, "launchType": launchType}, resources=[td.arn, t.task_arn])
        task_role = self.iam.role_by_arn(td.task_role_arn)
        creds = self.sts.assume_role(service_principal("ecs-tasks.amazonaws.com"), None, td.task_role_arn, task_id[:32],
                                     DurationSeconds=task_role.max_session_duration if task_role else 3600,
                                     SourceContext={"aws:SourceAccount": ACCOUNT, "aws:SourceArn": t.task_arn})
        t.credentials_access_key = creds.access_key_id
        t.last_status, t.started_at = "RUNNING", self.clock()
        try:
            t.result = td.entrypoint(TaskEnv(env, creds, t.task_arn, self.account))
            t.exit_code, t.stopped_reason = 0, "Essential container in task exited"
        except Exception as e:                                    # noqa: BLE001 - a crashed container is a STOPPED task
            t.exit_code = 1
            t.stopped_reason = f"Essential container in task exited: {type(e).__name__}: {e}"
            t.result = {"traceback": traceback.format_exc(limit=8)}
        t.last_status, t.stopped_at = "STOPPED", self.clock()
        return {"tasks": [{"taskArn": t.task_arn, "lastStatus": t.last_status}], "failures": []}

    def describe(self, task_arn: str) -> Task:
        return next(t for t in self.tasks if t.task_arn == task_arn)
