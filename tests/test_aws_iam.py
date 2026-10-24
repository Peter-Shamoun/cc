"""IAM policy evaluation: each rule the ClaimClarity policies rely on, checked against AWS's documented behaviour."""
import pytest

from ccsim.aws.iam import ACCOUNT, Principal, condition_ok, decide

ROLE = f"arn:aws:iam::{ACCOUNT}:role/cc-reader-acme_labs"
SESSION = f"arn:aws:sts::{ACCOUNT}:assumed-role/cc-reader-acme_labs/u-1"
P = Principal(SESSION, (SESSION, ROLE), "AssumedRole", ACCOUNT, {"customer": "acme_labs"}, ROLE, "u-1", 1000.0)
OTHER = Principal("arn:aws:sts::111122223333:assumed-role/admin/x", ("arn:aws:sts::111122223333:assumed-role/admin/x",
                  f"arn:aws:iam::{ACCOUNT}:role/admin"), "AssumedRole", ACCOUNT, {}, f"arn:aws:iam::{ACCOUNT}:role/admin", "x")
OBJ = "arn:aws:s3:::claimclarity-prod/acme_labs/semantic_layer/CURRENT"


def allow(action="s3:GetObject", resource="*", **extra):
    return {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": action, "Resource": resource, **extra}]}


def deny(action="s3:*", resource="*", **extra):
    return {"Version": "2012-10-17", "Statement": [{"Effect": "Deny", "Action": action, "Resource": resource, **extra}]}


def test_implicit_deny_without_any_allow():
    d = decide(P, "s3:GetObject", OBJ, {})
    assert not d and d.reason == "implicitDeny"


def test_identity_allow_and_wildcards():
    assert decide(P, "s3:GetObject", OBJ, {}, [allow("s3:Get*", "arn:aws:s3:::claimclarity-prod/acme_labs/*")])
    assert not decide(P, "s3:PutObject", OBJ, {}, [allow("s3:Get*", "arn:aws:s3:::claimclarity-prod/acme_labs/*")])
    assert not decide(P, "s3:GetObject", OBJ.replace("acme_labs", "acme_diagnostics"), {},
                      [allow("s3:GetObject", "arn:aws:s3:::claimclarity-prod/acme_labs/*")])


def test_action_names_are_case_insensitive_resources_are_not():
    assert decide(P, "s3:GetObject", OBJ, {}, [allow("S3:GETOBJECT", OBJ)])
    assert not decide(P, "s3:GetObject", OBJ, {}, [allow("s3:GetObject", OBJ.upper())])


def test_question_mark_matches_one_char_and_brackets_are_literal():
    assert decide(P, "s3:GetObject", "arn:aws:s3:::b/k1", {}, [allow(resource="arn:aws:s3:::b/k?")])
    assert not decide(P, "s3:GetObject", "arn:aws:s3:::b/k12", {}, [allow(resource="arn:aws:s3:::b/k?")])
    assert not decide(P, "s3:GetObject", "arn:aws:s3:::b/k1", {}, [allow(resource="arn:aws:s3:::b/k[0-9]")])


def test_explicit_deny_beats_every_allow():
    bp = {"Statement": [{"Effect": "Allow", "Principal": {"AWS": ROLE}, "Action": "s3:*", "Resource": "*"},
                        {"Effect": "Deny", "Principal": "*", "Action": "s3:GetObject", "Resource": OBJ}]}
    d = decide(P, "s3:GetObject", OBJ, {}, [allow("s3:*")], bp)
    assert not d and d.reason == "explicitDeny"


def test_not_action_and_not_resource():
    pol = {"Statement": [{"Effect": "Allow", "NotAction": "s3:Delete*", "Resource": "*"}]}
    assert decide(P, "s3:GetObject", OBJ, {}, [pol])
    assert not decide(P, "s3:DeleteObject", OBJ, {}, [pol])
    pol = {"Statement": [{"Effect": "Deny", "Action": "s3:*", "NotResource": "arn:aws:s3:::b/public/*"}]}
    assert not decide(P, "s3:GetObject", "arn:aws:s3:::b/private/x", {}, [allow("s3:*"), pol])
    assert decide(P, "s3:GetObject", "arn:aws:s3:::b/public/x", {}, [allow("s3:*"), pol])


def test_resource_policy_naming_role_grants_without_identity_policy():
    bp = {"Statement": [{"Effect": "Allow", "Principal": {"AWS": ROLE}, "Action": "s3:GetObject", "Resource": OBJ}]}
    assert decide(P, "s3:GetObject", OBJ, {}, [], bp)
    assert not decide(OTHER, "s3:GetObject", OBJ, {}, [], bp)


def test_resource_policy_naming_account_root_only_delegates():
    bp = {"Statement": [{"Effect": "Allow", "Principal": {"AWS": f"arn:aws:iam::{ACCOUNT}:root"}, "Action": "s3:*",
                         "Resource": "*"}]}
    assert not decide(P, "s3:GetObject", OBJ, {}, [], bp)            # root = "let IAM decide"
    assert decide(P, "s3:GetObject", OBJ, {}, [allow()], bp)


def test_kms_key_policy_is_authoritative():
    key = "arn:aws:kms:us-west-2:111122223333:key/k"
    admin_only = {"Statement": [{"Effect": "Allow", "Principal": {"AWS": f"arn:aws:iam::{ACCOUNT}:root"},
                                 "Action": ["kms:Describe*", "kms:Disable*"], "Resource": "*"}]}
    # identity policy kms:* is useless when the key policy does not delegate Decrypt
    assert not decide(OTHER, "kms:Decrypt", key, {}, [allow("kms:*")], admin_only, require_resource_allow=True)
    direct = {"Statement": [{"Effect": "Allow", "Principal": {"AWS": ROLE}, "Action": "kms:Decrypt", "Resource": "*"}]}
    assert decide(P, "kms:Decrypt", key, {}, [], direct, require_resource_allow=True)
    assert not decide(OTHER, "kms:Decrypt", key, {}, [allow("kms:*")], direct, require_resource_allow=True)
    delegated = {"Statement": [{"Effect": "Allow", "Principal": {"AWS": f"arn:aws:iam::{ACCOUNT}:root"},
                                "Action": "kms:*", "Resource": "*"}]}
    assert decide(OTHER, "kms:Decrypt", key, {}, [allow("kms:*")], delegated, require_resource_allow=True)
    assert not decide(OTHER, "kms:Decrypt", key, {}, [], delegated, require_resource_allow=True)


def test_session_policy_caps_identity_policy():
    sess = allow("s3:GetObject", "arn:aws:s3:::claimclarity-prod/acme_labs/semantic_layer/*")
    assert decide(P, "s3:GetObject", OBJ, {}, [allow("s3:*")], session_policies=[sess])
    assert not decide(P, "s3:GetObject", "arn:aws:s3:::claimclarity-prod/acme_labs/parquet/x", {}, [allow("s3:*")],
                      session_policies=[sess])


def test_permissions_boundary_caps_identity_policy():
    assert not decide(P, "s3:PutObject", OBJ, {}, [allow("s3:*")], boundary=allow("s3:GetObject"))
    assert decide(P, "s3:GetObject", OBJ, {}, [allow("s3:*")], boundary=allow("s3:GetObject"))


def test_not_principal_deny():
    bp = {"Statement": [{"Effect": "Deny", "NotPrincipal": {"AWS": ROLE}, "Action": "s3:*", "Resource": "*"}]}
    assert decide(P, "s3:GetObject", OBJ, {}, [allow("s3:*")], bp)
    assert not decide(OTHER, "s3:GetObject", OBJ, {}, [allow("s3:*")], bp)


def test_principal_arn_condition_pattern_used_by_bucket_policy():
    bp = {"Statement": [{"Effect": "Deny", "Principal": "*", "Action": "s3:*", "Resource": "*",
                         "Condition": {"StringNotLike": {"aws:PrincipalArn": [ROLE]}}}]}
    assert decide(P, "s3:GetObject", OBJ, {}, [allow("s3:*")], bp)
    assert not decide(OTHER, "s3:GetObject", OBJ, {}, [allow("s3:*")], bp)


def test_policy_variables():
    pol = allow(resource="arn:aws:s3:::claimclarity-prod/${aws:PrincipalTag/customer}/*")
    assert decide(P, "s3:GetObject", OBJ, {}, [pol])
    assert not decide(P, "s3:GetObject", OBJ.replace("acme_labs", "acme_diagnostics"), {}, [pol])
    assert not decide(OTHER, "s3:GetObject", OBJ, {}, [pol])         # unresolved variable never matches


@pytest.mark.parametrize("cond,ctx,ok", [
    ({"StringEquals": {"k": "a"}}, {"k": "a"}, True),
    ({"StringEquals": {"k": "a"}}, {"k": "A"}, False),
    ({"StringEqualsIgnoreCase": {"k": "a"}}, {"k": "A"}, True),
    ({"StringEquals": {"k": ["a", "b"]}}, {"k": "b"}, True),                 # values are OR-ed
    ({"StringEquals": {"k": "a", "j": "b"}}, {"k": "a", "j": "c"}, False),   # keys are AND-ed
    ({"StringEquals": {"k": "a"}}, {}, False),                               # missing key: no match
    ({"StringNotEquals": {"k": "a"}}, {}, True),                             # negated + missing: match
    ({"StringEqualsIfExists": {"k": "a"}}, {}, True),
    ({"StringLike": {"k": "acme_*/raw/*"}}, {"k": "acme_labs/raw/x"}, True),
    ({"Null": {"k": "true"}}, {}, True),
    ({"Null": {"k": "true"}}, {"k": "*"}, False),
    ({"Null": {"k": "false"}}, {"k": "*"}, True),
    ({"Bool": {"aws:SecureTransport": "false"}}, {"aws:SecureTransport": False}, True),
    ({"Bool": {"aws:SecureTransport": "false"}}, {"aws:SecureTransport": True}, False),
    ({"NumericLessThanEquals": {"s3:max-keys": "10"}}, {"s3:max-keys": 10}, True),
    ({"NumericLessThan": {"s3:max-keys": "10"}}, {"s3:max-keys": 10}, False),
    ({"DateLessThan": {"aws:TokenIssueTime": "2030-01-01T00:00:00Z"}}, {"aws:TokenIssueTime": 1_800_000_000}, True),
    ({"DateGreaterThan": {"aws:CurrentTime": "2030-01-01T00:00:00Z"}}, {"aws:CurrentTime": 1_800_000_000}, False),
    ({"IpAddress": {"aws:SourceIp": "10.0.0.0/8"}}, {"aws:SourceIp": "10.1.2.3"}, True),
    ({"NotIpAddress": {"aws:SourceIp": "10.0.0.0/8"}}, {"aws:SourceIp": "127.0.0.1"}, True),
    ({"ArnLike": {"aws:SourceArn": "arn:aws:ecs:*:111122223333:task/claimclarity/*"}},
     {"aws:SourceArn": "arn:aws:ecs:us-west-2:111122223333:task/claimclarity/abc"}, True),
    ({"ForAllValues:StringEquals": {"aws:TagKeys": ["customer", "user"]}}, {"aws:TagKeys": ["customer"]}, True),
    ({"ForAllValues:StringEquals": {"aws:TagKeys": ["customer"]}}, {"aws:TagKeys": ["customer", "admin"]}, False),
    ({"ForAnyValue:StringEquals": {"aws:TagKeys": ["admin"]}}, {"aws:TagKeys": ["customer", "admin"]}, True),
    ({"StringEquals": {"S3:Prefix": "a/"}}, {"s3:prefix": "a/"}, True),     # condition keys are case-insensitive
    ({"NoSuchOperator": {"k": "a"}}, {"k": "a"}, False),
])
def test_condition_operators(cond, ctx, ok):
    assert condition_ok(cond, ctx) is ok


def test_service_principal_and_federated_principal():
    svc = Principal("ecs-tasks.amazonaws.com", ("ecs-tasks.amazonaws.com",), "Service", service="ecs-tasks.amazonaws.com")
    trust = {"Statement": [{"Effect": "Allow", "Principal": {"Service": "ecs-tasks.amazonaws.com"}, "Action": "sts:AssumeRole"}]}
    assert decide(svc, "sts:AssumeRole", ROLE, {}, [], trust)
    assert not decide(P, "sts:AssumeRole", ROLE, {}, [], trust)
    assert not svc.matches({"AWS": f"arn:aws:iam::{ACCOUNT}:root"})         # a service is not "the account"
    fed = Principal("x", ("sub",), "WebIdentityUser", federated="arn:aws:iam::111122223333:oidc-provider/idp")
    assert fed.matches({"Federated": "arn:aws:iam::111122223333:oidc-provider/idp"})
