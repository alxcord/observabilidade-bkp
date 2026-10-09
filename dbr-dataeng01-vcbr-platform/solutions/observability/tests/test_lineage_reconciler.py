from __future__ import annotations

import hashlib
import json
import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from typing import Any
from unittest.mock import patch

from vc_observability.lineage.reconciler import (
    LEGACY_RELATIONSHIP_TYPE,
    MANAGED_BY,
    CustomLineageReconciler,
    main,
    parse_source_identity,
)


class Model:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


class FakeCatalog:
    class SystemType:
        SAP = "SAP"
        OTHER = "OTHER"

    class LineageDirection:
        UPSTREAM = "UPSTREAM"

    ExternalMetadata = Model
    ExternalLineageExternalMetadata = Model
    ExternalLineageTable = Model
    ExternalLineageObject = Model
    ColumnRelationship = Model
    CreateRequestExternalLineage = Model
    UpdateRequestExternalLineage = Model
    DeleteRequestExternalLineage = Model


class NotFound(Exception):
    pass


class CollectionApi:
    def __init__(self, values: list[Model] | dict[tuple[str, ...], list[Model]]) -> None:
        self.values = values
        self.failures: dict[tuple[str, ...], Exception] = {}

    def list(self, *args: str, **_kwargs: Any) -> list[Model]:
        key = tuple(args)
        if key in self.failures:
            raise self.failures[key]
        if isinstance(self.values, dict):
            return self.values.get(key, [])
        return self.values


class TableApi(CollectionApi):
    def get(self, full_name: str) -> Model:
        collections = self.values.values() if isinstance(self.values, dict) else [self.values]
        return next(
            item
            for values in collections
            for item in values
            if item.full_name == full_name
        )


class TagApi:
    def __init__(self, values: dict[tuple[str, str], dict[str, str]]) -> None:
        self.values = values
        self.failures: dict[tuple[str, str], Exception] = {}
        self.calls: list[tuple[str, str]] = []

    def list(self, entity_type: str, entity_name: str) -> list[Model]:
        key = (entity_type, entity_name)
        self.calls.append(key)
        if key in self.failures:
            raise self.failures[key]
        values = self.values.get(key, {})
        return [Model(tag_key=key, tag_value=value) for key, value in values.items()]


class MetadataApi:
    def __init__(self) -> None:
        self.values: dict[str, Model] = {}
        self.created: list[Model] = []
        self.updated: list[Model] = []
        self.fail_names: set[str] = set()

    def get_external_metadata(self, name: str) -> Model:
        if name not in self.values:
            raise NotFound(name)
        return self.values[name]

    def create_external_metadata(self, value: Model) -> None:
        if value.name in self.fail_names:
            raise RuntimeError("sem permissao")
        self.created.append(value)
        self.values[value.name] = value

    def update_external_metadata(self, _name: str, value: Model, _mask: str) -> None:
        self.updated.append(value)
        self.values[value.name] = value


class LineageApi:
    def __init__(self) -> None:
        self.values: dict[str, list[Model]] = {}
        self.created: list[Model] = []
        self.updated: list[Model] = []
        self.deleted: list[Model] = []

    def list_external_lineage_relationships(
        self,
        object_info: Model,
        _direction: str,
    ) -> list[Model]:
        return self.values.get(object_info.table.name, [])

    def create_external_lineage_relationship(self, request: Model) -> None:
        self.created.append(request)
        relationship = Model(
            id=f"lineage-{len(self.created)}",
            source=request.source,
            target=request.target,
            columns=request.columns,
            properties=request.properties,
        )
        self.values.setdefault(request.target.table.name, []).append(
            Model(external_lineage_info=relationship)
        )

    def update_external_lineage_relationship(self, request: Model, _mask: str) -> None:
        self.updated.append(request)
        relationships = self.values.get(request.target.table.name, [])
        for item in relationships:
            relationship = item.external_lineage_info
            if relationship.id == request.id:
                relationship.columns = request.columns
                relationship.properties = request.properties

    def delete_external_lineage_relationship(self, request: Model) -> None:
        self.deleted.append(request)
        relationships = self.values.get(request.target.table.name, [])
        self.values[request.target.table.name] = [
            item
            for item in relationships
            if item.external_lineage_info.id != request.id
        ]


class FakeWorkspace:
    def __init__(
        self,
        tables: list[Model],
        tags: dict[tuple[str, str], dict[str, str]],
        *,
        catalogs: list[str] | None = None,
        schemas: dict[str, list[str]] | None = None,
    ) -> None:
        catalog_names = catalogs or ["dev_logistics"]
        schema_names = schemas or {"dev_logistics": ["corp_raw"]}
        self.catalogs = CollectionApi([Model(name=name) for name in catalog_names])
        self.schemas = CollectionApi(
            {
                (catalog_name,): [Model(name=name) for name in names]
                for catalog_name, names in schema_names.items()
            }
        )
        tables_by_scope: dict[tuple[str, ...], list[Model]] = {}
        for value in tables:
            key = (value.catalog_name, value.schema_name)
            tables_by_scope.setdefault(key, []).append(value)
        self.tables = TableApi(tables_by_scope)
        self.entity_tag_assignments = TagApi(tags)
        self.external_metadata = MetadataApi()
        self.external_lineage = LineageApi()


def table(
    name: str,
    *,
    catalog: str = "dev_logistics",
    schema: str = "corp_raw",
) -> Model:
    full_name = f"{catalog}.{schema}.{name}"
    return Model(
        catalog_name=catalog,
        schema_name=schema,
        name=name,
        full_name=full_name,
        columns=[Model(name="plant")],
    )


def tags_for(
    name: str,
    source_table: str = "3_INTEGRACAO.R_PLANT",
    *,
    catalog: str = "dev_logistics",
    schema: str = "corp_raw",
) -> dict[tuple[str, str], dict[str, str]]:
    full_name = f"{catalog}.{schema}.{name}"
    return {
        ("tables", full_name): {
            "VC_SOURCE_SYSTEM": "DATASPHERE",
            "VC_SOURCE_OBJECT": source_table,
        },
        ("columns", f"{full_name}.plant"): {"VC_SOURCE_COLUMN": "Plant"},
    }


def existing_metadata(
    *,
    name: str = "datasphere_3_integracao_r_plant",
    columns: list[str] | None = None,
    properties: dict[str, str] | None = None,
) -> Model:
    return Model(
        name=name,
        system_type=FakeCatalog.SystemType.SAP,
        entity_type="VIEW",
        columns=columns,
        description="3_INTEGRACAO.R_PLANT",
        properties=(
            properties
            if properties is not None
            else {
                "managed_by": MANAGED_BY,
                "managed_version": "1",
                "source_system": "DATASPHERE",
                "source_schema": "3_INTEGRACAO",
                "source_table": "R_PLANT",
            }
        ),
        url=None,
    )


def lineage_relationship(
    target_name: str,
    *,
    relationship_id: str,
    source_name: str = "datasphere_3_integracao_r_plant",
    columns: list[Model] | None = None,
    properties: dict[str, str] | None = None,
) -> Model:
    return Model(
        external_lineage_info=Model(
            id=relationship_id,
            source=Model(external_metadata=Model(name=source_name)),
            target=Model(table=Model(name=target_name)),
            columns=columns,
            properties=(
                properties
                if properties is not None
                else {
                    "managed_by": MANAGED_BY,
                    "managed_version": "1",
                    "relationship_type": LEGACY_RELATIONSHIP_TYPE,
                    "source_external_id": "DATASPHERE:3_INTEGRACAO.R_PLANT",
                }
            ),
        )
    )


class CustomLineageReconcilerTests(unittest.TestCase):
    def test_missing_sdk_reports_actionable_install_command(self) -> None:
        with patch.dict(sys.modules, {"databricks": None}):
            with redirect_stdout(StringIO()) as output:
                exit_code = main([])

        summary = json.loads(output.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertIn("python -m pip install .", summary["error"])

    def test_dry_run_reports_changes_without_writes(self) -> None:
        workspace = FakeWorkspace([table("plant")], tags_for("plant"))

        summary = CustomLineageReconciler(workspace, FakeCatalog).run()

        self.assertEqual(summary["mode"], "dry-run")
        self.assertEqual(summary["counts"]["created"], 1)
        self.assertEqual(workspace.external_metadata.created, [])
        self.assertEqual(workspace.external_lineage.created, [])
        self.assertIn("create_external_metadata", summary["results"][0]["actions"])

    def test_apply_is_idempotent(self) -> None:
        workspace = FakeWorkspace([table("plant")], tags_for("plant"))
        reconciler = CustomLineageReconciler(workspace, FakeCatalog, apply=True)

        first = reconciler.run()
        second = reconciler.run()

        self.assertEqual(first["counts"]["created"], 1)
        self.assertEqual(second["counts"]["unchanged"], 1)
        self.assertEqual(len(workspace.external_metadata.created), 1)
        self.assertEqual(len(workspace.external_lineage.created), 1)
        request = workspace.external_lineage.created[0]
        self.assertEqual(request.source.external_metadata.name, "datasphere_3_integracao_r_plant")
        self.assertEqual(request.columns[0].source, "Plant")
        self.assertEqual(request.columns[0].target, "plant")

    def test_main_accepts_apply_mode_used_by_the_bundle_job(self) -> None:
        workspace = FakeWorkspace([table("plant")], tags_for("plant"))

        with redirect_stdout(StringIO()) as output:
            exit_code = main(
                ["--mode", "apply"],
                workspace_client=workspace,
                catalog_module=FakeCatalog,
            )

        summary = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(summary["mode"], "apply")
        self.assertEqual(len(workspace.external_metadata.created), 1)
        self.assertEqual(len(workspace.external_lineage.created), 1)

    def test_incomplete_tags_are_skipped(self) -> None:
        incomplete = tags_for("plant", "R_PLANT")
        workspace = FakeWorkspace([table("plant")], incomplete)

        summary = CustomLineageReconciler(workspace, FakeCatalog).run()

        self.assertEqual(summary["counts"]["skipped"], 1)
        self.assertIn("schema da origem ausente", summary["results"][0]["warnings"][0])

    def test_source_schema_tag_and_default_are_supported(self) -> None:
        tagged = parse_source_identity(
            {
                "VC_SOURCE_SYSTEM": "DATASPHERE",
                "VC_SOURCE_OBJECT": "R_PLANT",
                "SOURCE_SCHEMA": "3_INTEGRACAO",
            }
        )
        defaulted = parse_source_identity(
            {"VC_SOURCE_SYSTEM": "DATASPHERE", "VC_SOURCE_OBJECT": "R_PLANT"},
            "3_INTEGRACAO",
        )
        qualified = parse_source_identity(
            {
                "VC_SOURCE_SYSTEM": "DATASPHERE",
                "VC_SOURCE_OBJECT": "3_INTEGRACAO.R_PLANT",
                "SOURCE_SCHEMA": "IGNORED_SCHEMA",
            },
            "IGNORED_DEFAULT",
        )

        self.assertEqual(tagged.external_id, "DATASPHERE:3_INTEGRACAO.R_PLANT")
        self.assertEqual(defaulted, tagged)
        self.assertEqual(qualified, tagged)

    def test_catalog_and_schema_filters_limit_discovery(self) -> None:
        workspace = FakeWorkspace([table("plant")], tags_for("plant"))

        catalog_filtered = CustomLineageReconciler(
            workspace,
            FakeCatalog,
            catalogs=["another_catalog"],
        ).run()
        schema_filtered = CustomLineageReconciler(
            workspace,
            FakeCatalog,
            schemas=["another_schema"],
        ).run()

        self.assertEqual(catalog_filtered["scanned_tables"], 0)
        self.assertEqual(schema_filtered["scanned_tables"], 0)

    def test_corrected_tag_replaces_only_managed_stale_relationship(self) -> None:
        target = table("plant")
        workspace = FakeWorkspace([target], tags_for("plant"))
        target_object = Model(table=Model(name=target.full_name))
        stale = Model(
            id="stale",
            source=Model(external_metadata=Model(name="datasphere_3_integracao_old")),
            target=target_object,
            columns=None,
            properties={"relationship_type": LEGACY_RELATIONSHIP_TYPE},
        )
        manual = Model(
            id="manual",
            source=Model(external_metadata=Model(name="manual_asset")),
            target=target_object,
            columns=None,
            properties={"managed_by": "another_tool"},
        )
        workspace.external_lineage.values[target.full_name] = [
            Model(external_lineage_info=stale),
            Model(external_lineage_info=manual),
        ]

        summary = CustomLineageReconciler(workspace, FakeCatalog, apply=True).run()

        self.assertEqual([item.id for item in workspace.external_lineage.deleted], ["stale"])
        self.assertEqual(len(workspace.external_lineage.created), 1)
        self.assertEqual(
            workspace.external_lineage.created[0].source.external_metadata.name,
            "datasphere_3_integracao_r_plant",
        )
        self.assertIn("manual_asset", summary["results"][0]["warnings"][0])
        remaining_ids = [
            item.external_lineage_info.id
            for item in workspace.external_lineage.values[target.full_name]
        ]
        self.assertIn("manual", remaining_ids)

    def test_untagged_table_loses_only_its_managed_relationship(self) -> None:
        target = table("plant")
        workspace = FakeWorkspace([target], {})
        target_object = Model(table=Model(name=target.full_name))
        orphan = Model(
            id="orphan",
            source=Model(external_metadata=Model(name="datasphere_3_integracao_old")),
            target=target_object,
            columns=None,
            properties={"managed_by": MANAGED_BY},
        )
        manual = Model(
            id="manual",
            source=Model(external_metadata=Model(name="manual_asset")),
            target=target_object,
            columns=None,
            properties={"managed_by": "another_tool"},
        )
        workspace.external_lineage.values[target.full_name] = [
            Model(external_lineage_info=orphan),
            Model(external_lineage_info=manual),
        ]

        dry_run = CustomLineageReconciler(workspace, FakeCatalog, apply=False).run()
        self.assertEqual(workspace.external_lineage.deleted, [])
        self.assertEqual(dry_run["results"][0]["status"], "updated")
        self.assertEqual(
            dry_run["results"][0]["managed_relationships_planned_for_removal"][0]["reason"],
            "untagged_target",
        )

        summary = CustomLineageReconciler(workspace, FakeCatalog, apply=True).run()

        self.assertEqual([item.id for item in workspace.external_lineage.deleted], ["orphan"])
        self.assertEqual(summary["counts"]["updated"], 1)
        self.assertEqual(summary["results"][0]["target"], target.full_name)
        remaining_ids = [
            item.external_lineage_info.id
            for item in workspace.external_lineage.values[target.full_name]
        ]
        self.assertEqual(remaining_ids, ["manual"])

    def test_failure_isolated_and_reported(self) -> None:
        failing = table("bad")
        healthy = table("plant")
        combined_tags = tags_for("bad", "3_INTEGRACAO.R_BAD")
        combined_tags.update(tags_for("plant"))
        workspace = FakeWorkspace([failing, healthy], combined_tags)
        workspace.external_metadata.fail_names.add("datasphere_3_integracao_r_bad")

        summary = CustomLineageReconciler(workspace, FakeCatalog, apply=True).run()

        self.assertEqual(summary["scanned_tables"], 2)
        self.assertEqual(summary["counts"]["failed"], 1)
        self.assertEqual(summary["counts"]["created"], 1)
        self.assertIn("sem permissao", summary["results"][0]["error"])

    def test_malformed_table_does_not_interrupt_the_next_table(self) -> None:
        malformed = Model(
            catalog_name="dev_logistics",
            schema_name="corp_raw",
            name="",
            full_name="invalid",
            columns=[],
        )
        workspace = FakeWorkspace([malformed, table("plant")], tags_for("plant"))

        summary = CustomLineageReconciler(workspace, FakeCatalog).run()

        self.assertEqual(summary["scanned_tables"], 2)
        self.assertEqual(summary["counts"]["failed"], 1)
        self.assertEqual(summary["counts"]["created"], 1)
        self.assertEqual(summary["results"][0]["operation"], "identificacao da tabela")

    def test_column_tag_failure_does_not_mutate_any_state_for_table(self) -> None:
        target = table("plant")
        workspace = FakeWorkspace([target], tags_for("plant"))
        workspace.entity_tag_assignments.failures[(
            "columns",
            f"{target.full_name}.plant",
        )] = RuntimeError("tag indisponivel")

        summary = CustomLineageReconciler(workspace, FakeCatalog, apply=True).run()

        self.assertEqual(summary["counts"]["failed"], 1)
        self.assertEqual(
            summary["results"][0]["operation"],
            "leitura de tags de coluna",
        )
        self.assertEqual(workspace.external_metadata.created, [])
        self.assertEqual(workspace.external_metadata.updated, [])
        self.assertEqual(workspace.external_lineage.created, [])
        self.assertEqual(workspace.external_lineage.updated, [])
        self.assertEqual(workspace.external_lineage.deleted, [])

    def test_legacy_compatible_metadata_name_is_reused(self) -> None:
        workspace = FakeWorkspace([table("plant")], tags_for("plant"))
        name = "datasphere_3_integracao_r_plant"
        workspace.external_metadata.values[name] = existing_metadata(
            properties={
                "system": "DATASPHERE",
                "schema_name": "3_INTEGRACAO",
                "object_name": "R_PLANT",
            }
        )

        summary = CustomLineageReconciler(workspace, FakeCatalog, apply=True).run()

        self.assertEqual(summary["results"][0]["external_metadata_name"], name)
        self.assertEqual(workspace.external_metadata.created, [])
        self.assertEqual(len(workspace.external_metadata.updated), 1)
        self.assertEqual(workspace.external_lineage.created[0].source.external_metadata.name, name)

    def test_foreign_owned_metadata_uses_deterministic_hashed_name(self) -> None:
        workspace = FakeWorkspace([table("plant")], tags_for("plant"))
        base_name = "datasphere_3_integracao_r_plant"
        workspace.external_metadata.values[base_name] = existing_metadata(
            properties={
                "managed_by": "another_tool",
                "source_system": "DATASPHERE",
                "source_schema": "3_INTEGRACAO",
                "source_table": "R_PLANT",
            }
        )
        identity = ("datasphere", "3_integracao", "r_plant", "view")
        digest = hashlib.sha256("|".join(identity).encode("utf-8")).hexdigest()[:12]
        expected_name = f"{base_name}_{digest}"

        summary = CustomLineageReconciler(workspace, FakeCatalog, apply=True).run()

        self.assertEqual(summary["results"][0]["external_metadata_name"], expected_name)
        self.assertEqual(workspace.external_metadata.created[0].name, expected_name)
        self.assertEqual(
            workspace.external_metadata.values[base_name].properties["managed_by"],
            "another_tool",
        )

    def test_incompatible_hashed_metadata_is_not_taken_over(self) -> None:
        workspace = FakeWorkspace([table("plant")], tags_for("plant"))
        base_name = "datasphere_3_integracao_r_plant"
        identity = ("datasphere", "3_integracao", "r_plant", "view")
        digest = hashlib.sha256("|".join(identity).encode("utf-8")).hexdigest()[:12]
        hashed_name = f"{base_name}_{digest}"
        workspace.external_metadata.values[base_name] = existing_metadata(
            properties={"managed_by": "another_tool"}
        )
        workspace.external_metadata.values[hashed_name] = existing_metadata(
            name=hashed_name,
            properties={"managed_by": "another_tool"},
        )

        summary = CustomLineageReconciler(workspace, FakeCatalog, apply=True).run()

        self.assertEqual(summary["counts"]["failed"], 1)
        self.assertIn("colisao de nome", summary["results"][0]["error"])
        self.assertEqual(workspace.external_metadata.created, [])
        self.assertEqual(workspace.external_lineage.created, [])

    def test_dry_run_reserves_colliding_names_like_apply(self) -> None:
        first = table("first")
        second = table("second")
        first_tags = tags_for("first")
        second_tags = tags_for("second")
        first_tags[("tables", first.full_name)]["VC_SOURCE_SYSTEM"] = "A-B"
        second_tags[("tables", second.full_name)]["VC_SOURCE_SYSTEM"] = "A_B"
        combined_tags = {**first_tags, **second_tags}
        forward_workspace = FakeWorkspace([first, second], combined_tags)
        reverse_workspace = FakeWorkspace([second, first], combined_tags)
        apply_workspace = FakeWorkspace([first, second], combined_tags)

        forward = CustomLineageReconciler(forward_workspace, FakeCatalog).run()
        reverse = CustomLineageReconciler(reverse_workspace, FakeCatalog).run()
        applied = CustomLineageReconciler(
            apply_workspace,
            FakeCatalog,
            apply=True,
        ).run()

        def names_by_source(summary: dict[str, Any]) -> dict[str, str]:
            return {
                item["source"]: item["external_metadata_name"]
                for item in summary["results"]
            }

        forward_names = names_by_source(forward)
        self.assertEqual(forward_names, names_by_source(reverse))
        self.assertEqual(forward_names, names_by_source(applied))
        self.assertEqual(
            [item["source"] for item in forward["results"]],
            ["A-B:3_INTEGRACAO.R_PLANT", "A_B:3_INTEGRACAO.R_PLANT"],
        )
        self.assertEqual(
            [item["source"] for item in reverse["results"]],
            ["A_B:3_INTEGRACAO.R_PLANT", "A-B:3_INTEGRACAO.R_PLANT"],
        )
        self.assertEqual(
            forward_names["A-B:3_INTEGRACAO.R_PLANT"],
            "a_b_3_integracao_r_plant",
        )
        self.assertRegex(
            forward_names["A_B:3_INTEGRACAO.R_PLANT"],
            r"^a_b_3_integracao_r_plant_[0-9a-f]{12}$",
        )

    def test_relationships_created_by_script_include_ownership(self) -> None:
        workspace = FakeWorkspace([table("plant")], tags_for("plant"))

        CustomLineageReconciler(workspace, FakeCatalog, apply=True).run()

        relationship = workspace.external_lineage.created[0]
        self.assertEqual(relationship.properties["managed_by"], MANAGED_BY)

    def test_shared_external_asset_keeps_union_of_source_columns(self) -> None:
        first = table("plant")
        second = table("plant_text")
        second.columns = [Model(name="description")]
        combined_tags = tags_for("plant")
        combined_tags.update(tags_for("plant_text"))
        combined_tags[("columns", f"{second.full_name}.description")] = {
            "VC_SOURCE_COLUMN": "Description"
        }
        workspace = FakeWorkspace([first, second], combined_tags)
        reconciler = CustomLineageReconciler(workspace, FakeCatalog, apply=True)

        reconciler.run()
        second_run = reconciler.run()

        metadata = workspace.external_metadata.values["datasphere_3_integracao_r_plant"]
        self.assertEqual(metadata.columns, ["Description", "Plant"])
        self.assertEqual(second_run["counts"]["unchanged"], 2)

    def test_shared_external_asset_dry_run_reports_the_same_global_union(self) -> None:
        first = table("plant")
        second = table("plant_text")
        second.columns = [Model(name="description")]
        combined_tags = tags_for("plant")
        combined_tags.update(tags_for("plant_text"))
        combined_tags[("columns", f"{second.full_name}.description")] = {
            "VC_SOURCE_COLUMN": "Description"
        }
        workspace = FakeWorkspace([first, second], combined_tags)

        summary = CustomLineageReconciler(workspace, FakeCatalog).run()

        desired_columns = [
            result["desired_state"]["external_metadata"]["columns"]
            for result in summary["results"]
        ]
        self.assertEqual(desired_columns, [["Description", "Plant"]] * 2)
        self.assertEqual(summary["counts"]["created"], 2)
        self.assertEqual(workspace.external_metadata.created, [])

    def test_obsolete_metadata_column_is_removed(self) -> None:
        workspace = FakeWorkspace([table("plant")], tags_for("plant"))
        name = "datasphere_3_integracao_r_plant"
        workspace.external_metadata.values[name] = existing_metadata(
            columns=["Plant", "Obsolete"]
        )

        CustomLineageReconciler(workspace, FakeCatalog, apply=True).run()

        self.assertEqual(workspace.external_metadata.updated[0].columns, ["Plant"])

    def test_catalog_filter_preserves_existing_metadata_columns(self) -> None:
        workspace = FakeWorkspace([table("plant")], tags_for("plant"))
        name = "datasphere_3_integracao_r_plant"
        workspace.external_metadata.values[name] = existing_metadata(
            columns=["Plant", "OutsideFilter"]
        )

        summary = CustomLineageReconciler(
            workspace,
            FakeCatalog,
            apply=True,
            catalogs=["dev_logistics"],
        ).run()

        desired = summary["results"][0]["desired_state"]["external_metadata"]
        self.assertEqual(desired["columns"], ["OutsideFilter", "Plant"])
        self.assertEqual(workspace.external_metadata.updated, [])
        self.assertEqual(
            workspace.external_metadata.values[name].columns,
            ["Plant", "OutsideFilter"],
        )

    def test_discovery_failure_preserves_existing_metadata_columns(self) -> None:
        target = table("plant")
        workspace = FakeWorkspace(
            [target],
            tags_for("plant"),
            schemas={"dev_logistics": ["broken", "corp_raw"]},
        )
        workspace.tables.failures[("dev_logistics", "broken")] = RuntimeError(
            "tabelas indisponiveis"
        )
        name = "datasphere_3_integracao_r_plant"
        workspace.external_metadata.values[name] = existing_metadata(
            columns=["Plant", "UnknownScope"]
        )

        summary = CustomLineageReconciler(workspace, FakeCatalog, apply=True).run()

        self.assertEqual(summary["counts"]["failed"], 1)
        healthy_result = next(
            item for item in summary["results"] if item["target"] == target.full_name
        )
        self.assertEqual(
            healthy_result["desired_state"]["external_metadata"]["columns"],
            ["Plant", "UnknownScope"],
        )
        self.assertEqual(workspace.external_metadata.updated, [])
        self.assertEqual(
            workspace.external_metadata.values[name].columns,
            ["Plant", "UnknownScope"],
        )

    def test_managed_duplicate_is_removed_and_unmanaged_duplicate_is_preserved(self) -> None:
        target = table("plant")
        workspace = FakeWorkspace([target], tags_for("plant"))
        workspace.external_metadata.values[
            "datasphere_3_integracao_r_plant"
        ] = existing_metadata(columns=["Plant"])
        expected_columns = [Model(source="Plant", target="plant")]
        workspace.external_lineage.values[target.full_name] = [
            lineage_relationship(
                target.full_name,
                relationship_id="managed-primary",
                columns=expected_columns,
            ),
            lineage_relationship(
                target.full_name,
                relationship_id="managed-duplicate",
                columns=expected_columns,
            ),
            lineage_relationship(
                target.full_name,
                relationship_id="manual-duplicate",
                columns=expected_columns,
                properties={"managed_by": "another_tool"},
            ),
        ]

        summary = CustomLineageReconciler(workspace, FakeCatalog, apply=True).run()

        self.assertEqual(
            [request.id for request in workspace.external_lineage.deleted],
            ["managed-duplicate"],
        )
        remaining = {
            item.external_lineage_info.id
            for item in workspace.external_lineage.values[target.full_name]
        }
        self.assertEqual(remaining, {"managed-primary", "manual-duplicate"})
        self.assertIn("preservados", summary["results"][0]["warnings"][0])

    def test_managed_relationship_drift_is_updated(self) -> None:
        target = table("plant")
        workspace = FakeWorkspace([target], tags_for("plant"))
        workspace.external_metadata.values[
            "datasphere_3_integracao_r_plant"
        ] = existing_metadata(columns=["Plant"])
        workspace.external_lineage.values[target.full_name] = [
            lineage_relationship(
                target.full_name,
                relationship_id="managed",
                columns=[Model(source="OldPlant", target="plant")],
            )
        ]

        summary = CustomLineageReconciler(workspace, FakeCatalog, apply=True).run()

        self.assertEqual(summary["counts"]["updated"], 1)
        self.assertEqual(len(workspace.external_lineage.updated), 1)
        self.assertEqual(workspace.external_lineage.updated[0].columns[0].source, "Plant")

    def test_json_plan_contains_metadata_mappings_and_managed_removals(self) -> None:
        target = table("plant")
        workspace = FakeWorkspace([target], tags_for("plant"))
        workspace.external_lineage.values[target.full_name] = [
            lineage_relationship(
                target.full_name,
                relationship_id="stale",
                source_name="datasphere_3_integracao_old",
            )
        ]

        summary = CustomLineageReconciler(workspace, FakeCatalog).run()
        encoded = json.dumps(summary)
        result = summary["results"][0]

        self.assertIn("external_metadata", encoded)
        self.assertEqual(
            result["desired_state"]["external_lineage"]["column_mappings"],
            [{"source": "Plant", "target": "plant"}],
        )
        self.assertEqual(
            result["managed_relationships_planned_for_removal"][0]["id"],
            "stale",
        )

    def test_unmanaged_expected_relationship_is_not_modified(self) -> None:
        target = table("plant")
        workspace = FakeWorkspace([target], tags_for("plant"))
        relationship = Model(
            id="manual",
            source=Model(
                external_metadata=Model(name="datasphere_3_integracao_r_plant")
            ),
            target=Model(table=Model(name=target.full_name)),
            columns=None,
            properties={"managed_by": "another_tool"},
        )
        workspace.external_lineage.values[target.full_name] = [
            Model(external_lineage_info=relationship)
        ]

        summary = CustomLineageReconciler(workspace, FakeCatalog, apply=True).run()

        self.assertEqual(workspace.external_lineage.updated, [])
        self.assertEqual(workspace.external_lineage.deleted, [])
        self.assertIn("preservados", summary["results"][0]["warnings"][0])

    def test_apply_returns_non_zero_when_a_table_fails(self) -> None:
        workspace = FakeWorkspace([table("bad")], tags_for("bad", "3_INTEGRACAO.R_BAD"))
        workspace.external_metadata.fail_names.add("datasphere_3_integracao_r_bad")

        with redirect_stdout(StringIO()) as output:
            exit_code = main(
                ["--apply"],
                workspace_client=workspace,
                catalog_module=FakeCatalog,
            )

        self.assertEqual(exit_code, 1)
        self.assertEqual(json.loads(output.getvalue())["counts"]["failed"], 1)

    def test_main_accepts_all_filters_and_flags(self) -> None:
        selected = table("selected", catalog="keep", schema="raw")
        ignored = table("ignored", catalog="other", schema="raw")
        combined_tags = tags_for(
            "selected",
            "R_SELECTED",
            catalog="keep",
            schema="raw",
        )
        combined_tags.update(
            tags_for("ignored", catalog="other", schema="raw")
        )
        workspace = FakeWorkspace(
            [selected, ignored],
            combined_tags,
            catalogs=["keep", "other"],
            schemas={"keep": ["raw"], "other": ["raw"]},
        )

        with redirect_stdout(StringIO()) as output:
            exit_code = main(
                [
                    "--apply",
                    "--catalog",
                    "keep",
                    "--schema",
                    "keep.raw",
                    "--default-source-schema",
                    "SOURCE_SCHEMA",
                    "--skip-column-lineage",
                ],
                workspace_client=workspace,
                catalog_module=FakeCatalog,
            )

        summary = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(summary["scanned_tables"], 1)
        self.assertEqual(summary["results"][0]["source"], "DATASPHERE:SOURCE_SCHEMA.R_SELECTED")
        self.assertIsNone(workspace.external_lineage.created[0].columns)
        self.assertFalse(
            any(entity_type == "columns" for entity_type, _ in workspace.entity_tag_assignments.calls)
        )

    def test_main_ignores_empty_filters_from_bundle_variables(self) -> None:
        workspace = FakeWorkspace([table("plant")], tags_for("plant"))

        with redirect_stdout(StringIO()) as output:
            exit_code = main(
                ["--catalog", "", "--schema", ""],
                workspace_client=workspace,
                catalog_module=FakeCatalog,
            )

        summary = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(summary["scanned_tables"], 1)

    def test_catalog_and_schema_discovery_failures_do_not_stop_other_scopes(self) -> None:
        healthy = table("plant", catalog="good", schema="healthy")
        workspace = FakeWorkspace(
            [healthy],
            tags_for("plant", catalog="good", schema="healthy"),
            catalogs=["bad_catalog", "good"],
            schemas={"bad_catalog": ["ignored"], "good": ["broken", "healthy"]},
        )
        workspace.schemas.failures[("bad_catalog",)] = RuntimeError("schemas negados")
        workspace.tables.failures[("good", "broken")] = RuntimeError("tabelas negadas")

        summary = CustomLineageReconciler(workspace, FakeCatalog).run()

        self.assertEqual(summary["counts"]["failed"], 2)
        self.assertEqual(summary["counts"]["created"], 1)
        self.assertEqual(
            [result["target"] for result in summary["results"]],
            ["bad_catalog.*", "good.broken.*", healthy.full_name],
        )


if __name__ == "__main__":
    unittest.main()
