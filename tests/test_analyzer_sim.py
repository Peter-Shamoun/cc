"""Analyzer tools on the simulated query path: PHI, tenant scope, audited escalation.

Builds a small two-customer world through ``ccsim.world.build_world`` and opens sessions
only with ``ccsim.query.open_session`` (via ``QueryPathOpener``).
"""
import re

import pytest

from analyzer.config import AnalyzerConfig
from analyzer.opener import QueryPathOpener
from analyzer.tools import ToolError, ToolLayer
from ccsim.edi_mock import Scenario
from ccsim.query import login
from ccsim.world import build_world

PHI_COLUMNS = {"name_first", "name_last_or_org", "name_middle", "dmg_birth_date", "addr_line1", "addr_line2",
               "postal_code", "id_value", "member_id", "subscriber_id", "ssn",
               "bpr09_sender_account", "bpr15_receiver_account", "raw_text", "elements", "pat06_death_date"}


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    built = build_world(
        tmp_path_factory.mktemp("analyzer-world"),
        Scenario(days=3, volume_scale=0.05, seed=4, burn_in_days=0, p_corrupt_file=1.0, p_duplicate_835_file=0.0))
    yield built
    built.aws.shutdown()


def _tools(world, upn):
    opener = QueryPathOpener(world.aws, world.dep, login(world.aws, upn))
    config = AnalyzerConfig(glossary="basic", data_dictionary=False, data_profile=False, verify=False,
                            result_checks=False, self_correct="errors_only", max_tool_calls=200, row_limit=5000)
    return ToolLayer(opener, config)


def _patient_names(world) -> set[str]:
    names = set()
    for path in world.sftp_root.rglob("*.x12"):
        if "/837/" not in str(path):
            continue
        names |= {match.decode() for match in re.findall(rb"NM1[*|](?:IL|QC)[*|]1[*|]([^*|~]+)", path.read_bytes())}
    assert len(names) > 5
    return names


def test_tools_stay_inside_the_signed_in_tenant(world):
    labs = _tools(world, "ana@xifin.example")
    diagnostics = _tools(world, "dev@xifin.example")
    assert labs.session.customer == "acme_labs"
    assert diagnostics.session.customer == "acme_diagnostics"
    lab_rows = labs.run_sql("SELECT claim_key FROM sem.claims", include_all_rows=True)["all_rows"]
    dx_rows = diagnostics.run_sql("SELECT claim_key FROM sem.claims", include_all_rows=True)["all_rows"]
    lab_keys = [row[0] for row in lab_rows]
    dx_keys = [row[0] for row in dx_rows]
    assert lab_keys and dx_keys
    assert all(key.startswith("LB") for key in lab_keys)
    assert all(key.startswith("DX") for key in dx_keys)
    assert set(lab_keys).isdisjoint(dx_keys)
    with pytest.raises(ToolError):
        labs.run_sql("SELECT * FROM read_parquet('s3://cc-prod/acme_diagnostics/semantic_layer/sem.claims/schema/x.parquet')")
    with pytest.raises(ToolError):
        labs.run_sql("SELECT * FROM acme_diagnostics.sem.claims")


def test_tools_do_not_return_phi_and_escalation_is_audited(world):
    tools = _tools(world, "ana@xifin.example")
    names = _patient_names(world)
    for relation in tools.list_relations():
        described = tools.describe_relation(relation)
        columns = {column["name"] for column in described["columns"]}
        assert not columns & PHI_COLUMNS, relation
        if relation == "sem.claims":
            sample = tools.sample_rows(relation, 5)
            blob = " ".join(str(cell) for row in sample["rows"] for cell in row)
            assert not any(name in blob for name in names)
    with pytest.raises(ToolError):
        tools.run_sql("SELECT * FROM lossless.edi_entity")
    tools.request_escalation("file rejection counts are not on sem.*")
    granted = [record for record in tools.audit if record["event"] == "escalation_granted"]
    assert granted and granted[0]["customer"] == "acme_labs" and granted[0]["user"] == "ana@xifin.example"
    assert granted[0]["reason"] == "file rejection counts are not on sem.*"
    for relation in tools.list_relations():
        columns = {column["name"] for column in tools.describe_relation(relation)["columns"]}
        assert not columns & PHI_COLUMNS, relation
    rejected = tools.run_sql(
        "SELECT count(*) FROM lossless.edi_source_file WHERE parse_status = 'REJECTED'")["rows"]
    assert rejected and rejected[0][0] >= 1
