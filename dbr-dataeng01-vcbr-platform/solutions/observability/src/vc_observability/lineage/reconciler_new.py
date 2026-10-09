#!/usr/bin/env python3
"""Reconcilia External Metadata e External Lineage a partir de tags.

O reconciliador faz parte do pacote ``vc_observability`` e não depende do
``vc_extractor_engine``. O modo padrão é somente leitura; use ``--apply`` para
efetivar as mudanças planejadas.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, replace
from typing import Any, Iterable, Sequence


MANAGED_BY = "custom_lineage_reconciler"
MANAGED_VERSION = "1"
LEGACY_RELATIONSHIP_TYPE = "external_upstream_to_raw"
DEFAULT_EXCLUDED_CATALOGS = frozenset({"system", "samples", "__databricks_internal"})


@dataclass(frozen=True)
class ColumnMapping:
    """Mapeamento entre uma coluna externa e uma coluna Unity Catalog."""

    source: str
    target: str


@dataclass(frozen=True)
class SourceIdentity:
    """Identidade do objeto externo derivada das tags da tabela destino."""

    system: str
    schema: str
    table: str
    entity_type: str = "VIEW"
    resolved_metadata_name: str | None = None

    @property
    def external_id(self) -> str:
        return f"{self.system}:{self.schema}.{self.table}"

    @property
    def metadata_name(self) -> str:
        if self.resolved_metadata_name:
            return self.resolved_metadata_name
        return self.base_metadata_name

    @property
    def base_metadata_name(self) -> str:
        value = f"{self.system}_{self.schema}_{self.table}".lower()
        value = re.sub(r"[^a-z0-9_]+", "_", value).strip("_")
        return re.sub(r"_+", "_", value) or "external_source"

    @property
    def identity_key(self) -> tuple[str, str, str, str]:
        return (
            self.system.casefold(),
            self.schema.casefold(),
            self.table.casefold(),
            self.entity_type.casefold(),
        )

    def with_metadata_name(self, name: str) -> "SourceIdentity":
        return replace(self, resolved_metadata_name=name)


@dataclass(frozen=True)
class TargetIdentity:
    """Tabela Unity Catalog candidata a receber lineage externo."""

    catalog: str
    schema: str
    table: str

    @property
    def full_name(self) -> str:
        return f"{self.catalog}.{self.schema}.{self.table}"


@dataclass(frozen=True)
class ReconciliationCandidate:
    """Estado desejado calculado para uma tabela marcada."""

    source: SourceIdentity
    target: TargetIdentity
    columns: tuple[ColumnMapping, ...] = ()


def _clean_identifier(value: str) -> str:
    return value.strip().strip("`\"")


def parse_source_identity(
    tags: dict[str, str],
    default_source_schema: str | None = None,
) -> SourceIdentity:
    """Converte tags tecnicas em uma identidade externa validada."""

    normalized = {str(key).upper(): str(value).strip() for key, value in tags.items()}
    system = normalized.get("VC_SOURCE_SYSTEM", "")
    source_table = normalized.get("VC_SOURCE_OBJECT", "")
    if not system:
        raise ValueError("tag vc_source_system ausente ou vazia")
    if not source_table:
        raise ValueError("tag vc_source_object ausente ou vazia")

    if "." in source_table:
        schema, table = source_table.rsplit(".", 1)
        schema = _clean_identifier(schema)
        table = _clean_identifier(table)
    else:
        schema = _clean_identifier(
            normalized.get("SOURCE_SCHEMA", "") or (default_source_schema or "")
        )
        table = _clean_identifier(source_table)
    if not schema:
        raise ValueError(
            "schema da origem ausente; qualifique vc_source_object, use SOURCE_SCHEMA "
            "ou informe --default-source-schema"
        )
    if not table:
        raise ValueError("nome da tabela de origem vazio")

    entity_type = normalized.get("SOURCE_OBJECT_TYPE", "VIEW").upper()
    if entity_type not in {"TABLE", "VIEW"}:
        raise ValueError("SOURCE_OBJECT_TYPE deve ser TABLE ou VIEW")
    return SourceIdentity(
        system=system,
        schema=schema,
        table=table,
        entity_type=entity_type,
    )


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _is_not_found(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    return status_code == 404 or exc.__class__.__name__.lower() in {
        "notfound",
        "resourcedoesnotexist",
    }


def _object_external_name(value: Any) -> str | None:
    external = getattr(value, "external_metadata", None)
    return getattr(external, "name", None) if external is not None else None


def _object_table_name(value: Any) -> str | None:
    table = getattr(value, "table", None)
    return getattr(table, "name", None) if table is not None else None


def _column_pairs(values: Iterable[Any] | None) -> set[tuple[str, str]]:
    return {
        (str(getattr(value, "source", "")), str(getattr(value, "target", "")))
        for value in values or ()
    }


def _is_managed(properties: dict[str, Any]) -> bool:
    """Relacionamento criado por este processo, inclusive o formato legado."""
    return (
        properties.get("managed_by") == MANAGED_BY
        or properties.get("relationship_type") == LEGACY_RELATIONSHIP_TYPE
    )


class CustomLineageReconciler:
    """Descobre tabelas marcadas e reconcilia seu lineage externo."""

    def __init__(
        self,
        workspace_client: Any,
        catalog_module: Any,
        *,
        apply: bool = False,
        catalogs: Sequence[str] = (),
        schemas: Sequence[str] = (),
        default_source_schema: str | None = None,
        include_column_lineage: bool = True,
    ) -> None:
        self.client = workspace_client
        self.catalog = catalog_module
        self.apply = apply
        self.catalog_filter = {value.casefold() for value in catalogs}
        self.schema_filter = {value.casefold() for value in schemas}
        self.default_source_schema = default_source_schema
        self.include_column_lineage = include_column_lineage

    def run(self) -> dict[str, Any]:
        """Executa descoberta e reconciliacao, isolando falhas por tabela."""

        ordered_results: list[tuple[int, dict[str, Any]]] = []
        candidates: list[tuple[int, ReconciliationCandidate]] = []
        inventory_complete = not (self.catalog_filter or self.schema_filter)
        scanned = 0
        tagged = 0
        order = 0
        for table, scope, discovery_error in self._table_entries():
            if discovery_error is not None:
                inventory_complete = False
                ordered_results.append(
                    (order, self._failure(scope, "descoberta de tabelas", discovery_error))
                )
                order += 1
                continue
            scanned += 1
            try:
                target = self._target(table)
            except Exception as exc:
                inventory_complete = False
                table_hint = str(getattr(table, "full_name", "") or "<tabela desconhecida>")
                ordered_results.append(
                    (order, self._failure(table_hint, "identificacao da tabela", exc))
                )
                order += 1
                continue
            try:
                tags = self._tags("tables", target.full_name)
            except Exception as exc:
                inventory_complete = False
                ordered_results.append(
                    (order, self._failure(target.full_name, "leitura de tags", exc))
                )
                order += 1
                continue
            if not ({"VC_SOURCE_SYSTEM", "VC_SOURCE_OBJECT"} & set(tags)):
                orphan = self._sweep_orphan_relationships(target, "tabela sem tags de origem")
                if orphan is not None:
                    ordered_results.append((order, orphan))
                    order += 1
                continue
            tagged += 1
            try:
                source = parse_source_identity(tags, self.default_source_schema)
            except ValueError as exc:
                inventory_complete = False
                ordered_results.append(
                    (
                        order,
                        {
                            "target": target.full_name,
                            "status": "skipped",
                            "warnings": [str(exc)],
                        },
                    )
                )
                order += 1
                orphan = self._sweep_orphan_relationships(target, "identidade de origem invalida")
                if orphan is not None:
                    ordered_results.append((order, orphan))
                    order += 1
                continue
            try:
                columns = self._column_mappings(table, target)
            except Exception as exc:
                inventory_complete = False
                ordered_results.append(
                    (order, self._failure(target.full_name, "leitura de tags de coluna", exc))
                )
                order += 1
                continue
            candidates.append((order, ReconciliationCandidate(source, target, columns)))
            order += 1

        groups: dict[tuple[str, str, str, str], list[tuple[int, ReconciliationCandidate]]] = {}
        for item in candidates:
            groups.setdefault(item[1].source.identity_key, []).append(item)

        metadata_name_reservations: dict[str, tuple[str, str, str, str]] = {}
        for source_key in sorted(groups):
            group = groups[source_key]
            source = group[0][1].source
            metadata_columns = tuple(
                sorted(
                    {
                        mapping.source
                        for _, candidate in group
                        for mapping in candidate.columns
                    }
                )
            )
            try:
                source, existing_metadata = self._resolve_metadata_source(
                    source,
                    metadata_name_reservations,
                )
                metadata_name_reservations[
                    source.metadata_name.casefold()
                ] = source.identity_key
                if not inventory_complete and existing_metadata is not None:
                    metadata_columns = tuple(
                        sorted(
                            set(metadata_columns)
                            | set(getattr(existing_metadata, "columns", None) or ())
                        )
                    )
                desired_metadata = self._desired_metadata(
                    source,
                    metadata_columns,
                    existing=existing_metadata,
                )
                metadata_actions = self._apply_metadata(
                    source,
                    existing_metadata,
                    desired_metadata,
                )
            except Exception as exc:
                for item_order, candidate in group:
                    ordered_results.append(
                        (
                            item_order,
                            self._failure(
                                candidate.target.full_name,
                                "reconciliacao de external metadata",
                                exc,
                            ),
                        )
                    )
                continue

            for group_index, (item_order, candidate) in enumerate(group):
                candidate = replace(candidate, source=source)
                try:
                    ordered_results.append(
                        (
                            item_order,
                            self._reconcile_relationship(
                                candidate,
                                desired_metadata,
                                metadata_actions if group_index == 0 else (),
                                existing_metadata is not None,
                            ),
                        )
                    )
                except Exception as exc:
                    ordered_results.append(
                        (
                            item_order,
                            self._failure(candidate.target.full_name, "reconciliacao", exc),
                        )
                    )

        results = [result for _, result in sorted(ordered_results, key=lambda item: item[0])]
        counts = {status: 0 for status in ("created", "updated", "unchanged", "skipped", "failed")}
        for result in results:
            status = str(result["status"])
            counts[status] = counts.get(status, 0) + 1
        return {
            "mode": "apply" if self.apply else "dry-run",
            "scanned_tables": scanned,
            "tagged_tables": tagged,
            "counts": counts,
            "results": results,
        }

    def _table_entries(self) -> Iterable[tuple[Any | None, str, Exception | None]]:
        explicit_catalogs = bool(self.catalog_filter)
        for catalog_info in self.client.catalogs.list():
            catalog_name = str(getattr(catalog_info, "name", "") or "")
            if not catalog_name:
                continue
            if self.catalog_filter and catalog_name.casefold() not in self.catalog_filter:
                continue
            if not explicit_catalogs and catalog_name.casefold() in DEFAULT_EXCLUDED_CATALOGS:
                continue
            try:
                schemas = list(self.client.schemas.list(catalog_name))
            except Exception as exc:
                yield None, f"{catalog_name}.*", exc
                continue
            for schema_info in schemas:
                schema_name = str(getattr(schema_info, "name", "") or "")
                if not schema_name or schema_name.casefold() == "information_schema":
                    continue
                qualified_schema = f"{catalog_name}.{schema_name}".casefold()
                if self.schema_filter and not (
                    schema_name.casefold() in self.schema_filter
                    or qualified_schema in self.schema_filter
                ):
                    continue
                try:
                    tables = list(
                        self.client.tables.list(
                            catalog_name,
                            schema_name,
                            omit_columns=not self.include_column_lineage,
                        )
                    )
                except Exception as exc:
                    yield None, f"{catalog_name}.{schema_name}.*", exc
                    continue
                for table in tables:
                    yield table, str(getattr(table, "full_name", "") or "<tabela>"), None

    def _target(self, table: Any) -> TargetIdentity:
        catalog_name = str(getattr(table, "catalog_name", "") or "")
        schema_name = str(getattr(table, "schema_name", "") or "")
        table_name = str(getattr(table, "name", "") or "")
        if not all((catalog_name, schema_name, table_name)):
            full_name = str(getattr(table, "full_name", "") or "")
            parts = full_name.split(".")
            if len(parts) != 3:
                raise ValueError(f"tabela sem nome completo valido: {full_name!r}")
            catalog_name, schema_name, table_name = parts
        return TargetIdentity(catalog_name, schema_name, table_name)

    def _tags(self, entity_type: str, entity_name: str) -> dict[str, str]:
        return {
            str(item.tag_key).upper(): str(item.tag_value or "")
            for item in self.client.entity_tag_assignments.list(entity_type, entity_name)
        }

    def _column_mappings(
        self,
        table: Any,
        target: TargetIdentity,
    ) -> tuple[ColumnMapping, ...]:
        if not self.include_column_lineage:
            return ()
        columns = getattr(table, "columns", None)
        if columns is None:
            table = self.client.tables.get(target.full_name)
            columns = getattr(table, "columns", None)
        mappings: list[ColumnMapping] = []
        for column in columns or ():
            column_name = str(getattr(column, "name", "") or "")
            if not column_name:
                continue
            try:
                tags = self._tags("columns", f"{target.full_name}.{column_name}")
            except Exception as exc:
                raise RuntimeError(
                    f"nao foi possivel ler tags da coluna {column_name}: {exc}"
                ) from exc
            source_column = tags.get("VC_SOURCE_COLUMN", "").strip()
            if source_column:
                mappings.append(ColumnMapping(source_column, column_name))
        return tuple(mappings)

    def _resolve_metadata_source(
        self,
        source: SourceIdentity,
        reservations: dict[str, tuple[str, str, str, str]],
    ) -> tuple[SourceIdentity, Any | None]:
        base_metadata = self._get_metadata(source.base_metadata_name)
        base_reservation = reservations.get(source.base_metadata_name.casefold())
        base_available = base_reservation in {None, source.identity_key}
        if base_available and (
            base_metadata is None or self._can_reuse_metadata(base_metadata, source)
        ):
            return source.with_metadata_name(source.base_metadata_name), base_metadata

        digest = hashlib.sha256(
            "|".join(source.identity_key).encode("utf-8")
        ).hexdigest()[:12]
        disambiguated_name = f"{source.base_metadata_name}_{digest}"
        disambiguated = self._get_metadata(disambiguated_name)
        disambiguated_reservation = reservations.get(disambiguated_name.casefold())
        disambiguated_available = disambiguated_reservation in {
            None,
            source.identity_key,
        }
        if not disambiguated_available or (
            disambiguated is not None
            and not self._can_reuse_metadata(disambiguated, source)
        ):
            raise RuntimeError(
                "colisao de nome de External Metadata sem identidade compativel: "
                f"{disambiguated_name}"
            )
        return source.with_metadata_name(disambiguated_name), disambiguated

    def _can_reuse_metadata(self, metadata: Any, source: SourceIdentity) -> bool:
        properties = dict(getattr(metadata, "properties", None) or {})
        owner = str(properties.get("managed_by") or "")
        identity = self._metadata_identity(properties)
        compatible = identity == source.identity_key[:3]
        entity_type = str(_enum_value(getattr(metadata, "entity_type", None)) or "")
        compatible_type = not entity_type or entity_type.casefold() == source.entity_type.casefold()
        return compatible and compatible_type and owner in {"", MANAGED_BY}

    def _metadata_identity(
        self,
        properties: dict[str, Any],
    ) -> tuple[str, str, str] | None:
        external_id = str(properties.get("source_external_id") or "")
        if ":" in external_id and "." in external_id:
            system, qualified = external_id.split(":", 1)
            schema, table = qualified.rsplit(".", 1)
            return (system.casefold(), schema.casefold(), table.casefold())
        system = str(properties.get("source_system") or properties.get("system") or "")
        schema = str(properties.get("source_schema") or properties.get("schema_name") or "")
        table = str(properties.get("source_table") or properties.get("object_name") or "")
        if not all((system, schema, table)):
            return None
        return (system.casefold(), schema.casefold(), table.casefold())

    def _apply_metadata(
        self,
        source: SourceIdentity,
        existing_metadata: Any | None,
        metadata: Any,
    ) -> tuple[str, ...]:
        actions: list[str] = []
        if existing_metadata is None:
            actions.append("create_external_metadata")
            if self.apply:
                self.client.external_metadata.create_external_metadata(metadata)
        elif self._metadata_changed(existing_metadata, metadata):
            actions.append("update_external_metadata")
            if self.apply:
                self.client.external_metadata.update_external_metadata(
                    source.metadata_name,
                    metadata,
                    "system_type,entity_type,columns,description,properties,url",
                )
        return tuple(actions)

    def _reconcile_relationship(
        self,
        candidate: ReconciliationCandidate,
        desired_metadata: Any,
        metadata_actions: Sequence[str],
        metadata_existed: bool,
    ) -> dict[str, Any]:
        actions = list(metadata_actions)
        source = candidate.source
        target = candidate.target
        lineage_object = self.catalog.ExternalLineageObject(
            table=self.catalog.ExternalLineageTable(name=target.full_name)
        )
        relationships = list(
            self.client.external_lineage.list_external_lineage_relationships(
                lineage_object,
                self.catalog.LineageDirection.UPSTREAM,
            )
        )
        managed_expected: list[Any] = []
        unmanaged_expected: list[Any] = []
        stale_managed: list[Any] = []
        unmanaged_conflicts: list[str] = []
        for item in relationships:
            relationship = getattr(item, "external_lineage_info", None) or item
            source_name = _object_external_name(getattr(relationship, "source", None))
            if not source_name:
                continue
            properties = dict(getattr(relationship, "properties", None) or {})
            managed = _is_managed(properties)
            if source_name == source.metadata_name:
                if managed:
                    managed_expected.append(relationship)
                else:
                    unmanaged_expected.append(relationship)
                    unmanaged_conflicts.append(source_name)
            elif managed:
                stale_managed.append(relationship)
            else:
                unmanaged_conflicts.append(source_name)

        expected = managed_expected[0] if managed_expected else (
            unmanaged_expected[0] if unmanaged_expected else None
        )
        duplicate_managed = managed_expected[1:]
        desired_request = self._lineage_request(candidate, existing=expected)
        if expected is None:
            actions.append("create_external_lineage")
            if self.apply:
                self.client.external_lineage.create_external_lineage_relationship(desired_request)
        elif self._lineage_changed(expected, desired_request):
            if managed_expected:
                actions.append("update_external_lineage")
                if self.apply:
                    update_request = self.catalog.UpdateRequestExternalLineage(
                        source=desired_request.source,
                        target=desired_request.target,
                        columns=desired_request.columns,
                        properties=desired_request.properties,
                        id=getattr(expected, "id", None),
                    )
                    self.client.external_lineage.update_external_lineage_relationship(
                        update_request,
                        "columns,properties",
                    )
            else:
                if source.metadata_name not in unmanaged_conflicts:
                    unmanaged_conflicts.append(source.metadata_name)

        relationships_to_delete = [
            (relationship, "stale_source") for relationship in stale_managed
        ] + [
            (relationship, "duplicate") for relationship in duplicate_managed
        ]
        planned_removals: list[dict[str, Any]] = []
        for relationship, reason in relationships_to_delete:
            stale_name = _object_external_name(getattr(relationship, "source", None)) or "unknown"
            action_prefix = (
                "delete_duplicate_external_lineage"
                if reason == "duplicate"
                else "delete_stale_external_lineage"
            )
            actions.append(f"{action_prefix}:{stale_name}")
            planned_removals.append(
                {
                    "id": getattr(relationship, "id", None),
                    "source_external_metadata": stale_name,
                    "target_table": _object_table_name(
                        getattr(relationship, "target", None)
                    ),
                    "reason": reason,
                }
            )
            if self.apply:
                self._delete_relationship(relationship)

        status = "unchanged"
        if actions:
            status = "updated" if metadata_existed or expected is not None else "created"
        result: dict[str, Any] = {
            "target": target.full_name,
            "source": source.external_id,
            "external_metadata_name": source.metadata_name,
            "status": status,
            "actions": actions,
            "desired_state": {
                "external_metadata": self._metadata_payload(desired_metadata),
                "external_lineage": self._lineage_payload(desired_request),
            },
            "managed_relationships_planned_for_removal": planned_removals,
        }
        if unmanaged_conflicts:
            result["warnings"] = [
                "Relacionamentos nao gerenciados foram preservados: "
                + ", ".join(sorted(unmanaged_conflicts))
            ]
        return result

    def _sweep_orphan_relationships(
        self,
        target: TargetIdentity,
        motivo: str,
    ) -> dict[str, Any] | None:
        """Remove o lineage gerenciado de uma tabela que deixou de ser declarada.

        Quando a tag some (ou a origem deixa de ser valida), o relacionamento
        criado antes continuaria no Unity Catalog, apontando para uma origem
        que a tabela nao declara mais. So sai o que este processo gerenciou;
        relacionamento de outra ferramenta fica intacto. Sem nada a remover,
        nao ha resultado a reportar.
        """
        try:
            relationships = list(
                self.client.external_lineage.list_external_lineage_relationships(
                    self.catalog.ExternalLineageObject(
                        table=self.catalog.ExternalLineageTable(name=target.full_name)
                    ),
                    self.catalog.LineageDirection.UPSTREAM,
                )
            )
        except Exception as exc:
            return self._failure(target.full_name, "leitura de lineage de tabela sem tag", exc)

        orphans = []
        for item in relationships:
            relationship = getattr(item, "external_lineage_info", None) or item
            properties = dict(getattr(relationship, "properties", None) or {})
            if not _is_managed(properties):
                continue
            if not _object_external_name(getattr(relationship, "source", None)):
                continue
            orphans.append(relationship)
        if not orphans:
            return None

        actions: list[str] = []
        planned_removals: list[dict[str, Any]] = []
        for relationship in orphans:
            stale_name = _object_external_name(getattr(relationship, "source", None))
            actions.append(f"delete_orphan_external_lineage:{stale_name}")
            planned_removals.append(
                {
                    "id": getattr(relationship, "id", None),
                    "source_external_metadata": stale_name,
                    "target_table": target.full_name,
                    "reason": "untagged_target",
                }
            )
            if self.apply:
                try:
                    self._delete_relationship(relationship)
                except Exception as exc:
                    return self._failure(target.full_name, "remocao de lineage de tabela sem tag", exc)
        return {
            "target": target.full_name,
            "status": "updated",
            "reason": motivo,
            "actions": actions,
            "managed_relationships_planned_for_removal": planned_removals,
        }

    def _delete_relationship(self, relationship: Any) -> None:
        request = self.catalog.DeleteRequestExternalLineage(
            source=relationship.source,
            target=relationship.target,
            id=getattr(relationship, "id", None),
        )
        self.client.external_lineage.delete_external_lineage_relationship(request)

    def _get_metadata(self, name: str) -> Any | None:
        try:
            return self.client.external_metadata.get_external_metadata(name)
        except Exception as exc:
            if _is_not_found(exc):
                return None
            raise

    def _desired_metadata(
        self,
        source: SourceIdentity,
        columns: Sequence[str],
        existing: Any | None,
    ) -> Any:
        properties = dict(getattr(existing, "properties", None) or {})
        properties.update(
            {
                "managed_by": MANAGED_BY,
                "managed_version": MANAGED_VERSION,
                "source_system": source.system,
                "source_schema": source.schema,
                "source_table": source.table,
            }
        )
        system_type = (
            self.catalog.SystemType.SAP
            if source.system.casefold() == "datasphere" or "sap" in source.system.casefold()
            else self.catalog.SystemType.OTHER
        )
        return self.catalog.ExternalMetadata(
            name=source.metadata_name,
            system_type=system_type,
            entity_type=source.entity_type,
            columns=list(columns) or None,
            description=f"{source.schema}.{source.table}",
            properties=properties,
            url=getattr(existing, "url", None),
        )

    def _lineage_request(self, candidate: ReconciliationCandidate, existing: Any | None) -> Any:
        source = self.catalog.ExternalLineageObject(
            external_metadata=self.catalog.ExternalLineageExternalMetadata(
                name=candidate.source.metadata_name
            )
        )
        target = self.catalog.ExternalLineageObject(
            table=self.catalog.ExternalLineageTable(name=candidate.target.full_name)
        )
        columns = [
            self.catalog.ColumnRelationship(source=item.source, target=item.target)
            for item in candidate.columns
        ]
        properties = dict(getattr(existing, "properties", None) or {})
        properties.update(
            {
                "managed_by": MANAGED_BY,
                "managed_version": MANAGED_VERSION,
                "relationship_type": LEGACY_RELATIONSHIP_TYPE,
                "source_external_id": candidate.source.external_id,
            }
        )
        return self.catalog.CreateRequestExternalLineage(
            source=source,
            target=target,
            columns=columns or None,
            properties=properties,
        )

    def _metadata_changed(self, actual: Any, desired: Any) -> bool:
        return any(
            (
                _enum_value(getattr(actual, field, None))
                != _enum_value(getattr(desired, field, None))
            )
            for field in ("system_type", "entity_type", "description", "url")
        ) or set(getattr(actual, "columns", None) or ()) != set(desired.columns or ()) or any(
            (getattr(actual, "properties", None) or {}).get(key) != value
            for key, value in (desired.properties or {}).items()
        )

    def _lineage_changed(self, actual: Any, desired: Any) -> bool:
        return _column_pairs(getattr(actual, "columns", None)) != _column_pairs(desired.columns) or any(
            (getattr(actual, "properties", None) or {}).get(key) != value
            for key, value in (desired.properties or {}).items()
        )

    def _metadata_payload(self, metadata: Any) -> dict[str, Any]:
        return {
            "name": getattr(metadata, "name", None),
            "system_type": _enum_value(getattr(metadata, "system_type", None)),
            "entity_type": getattr(metadata, "entity_type", None),
            "columns": list(getattr(metadata, "columns", None) or ()),
            "description": getattr(metadata, "description", None),
            "properties": dict(getattr(metadata, "properties", None) or {}),
            "url": getattr(metadata, "url", None),
        }

    def _lineage_payload(self, lineage: Any) -> dict[str, Any]:
        return {
            "source_external_metadata": _object_external_name(
                getattr(lineage, "source", None)
            ),
            "target_table": _object_table_name(getattr(lineage, "target", None)),
            "column_mappings": [
                {"source": source, "target": target}
                for source, target in sorted(_column_pairs(getattr(lineage, "columns", None)))
            ],
            "properties": dict(getattr(lineage, "properties", None) or {}),
        }

    def _failure(self, target: str, operation: str, exc: Exception) -> dict[str, Any]:
        return {
            "target": target,
            "status": "failed",
            "operation": operation,
            "error": str(exc),
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reconcilia External Lineage a partir de tags do Unity Catalog."
    )
    execution_mode = parser.add_mutually_exclusive_group()
    execution_mode.add_argument(
        "--apply",
        action="store_true",
        help="Efetiva as alteracoes planejadas; mantido para compatibilidade.",
    )
    execution_mode.add_argument(
        "--mode",
        choices=("dry-run", "apply"),
        default="dry-run",
        help="Define se a execucao apenas planeja ou efetiva as alteracoes.",
    )
    parser.add_argument("--catalog", action="append", default=[], help="Catalogo a incluir; repetivel.")
    parser.add_argument("--schema", action="append", default=[], help="Schema ou catalog.schema a incluir; repetivel.")
    parser.add_argument(
        "--default-source-schema",
        help="Schema externo usado quando vc_source_object nao estiver qualificada.",
    )
    parser.add_argument(
        "--skip-column-lineage",
        action="store_true",
        help="Nao consulta vc_source_column nem publica mapeamento de colunas.",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    workspace_client: Any | None = None,
    catalog_module: Any | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    apply_changes = args.apply or args.mode == "apply"
    try:
        if workspace_client is None or catalog_module is None:
            try:
                from databricks.sdk import WorkspaceClient
                from databricks.sdk.service import catalog
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    "Dependência ausente. Instale o pacote com suas dependências: "
                    "python -m pip install ."
                ) from exc

            workspace_client = workspace_client or WorkspaceClient()
            catalog_module = catalog_module or catalog
        summary = CustomLineageReconciler(
            workspace_client,
            catalog_module,
            apply=apply_changes,
            catalogs=[value.strip() for value in args.catalog if value.strip()],
            schemas=[value.strip() for value in args.schema if value.strip()],
            default_source_schema=args.default_source_schema,
            include_column_lineage=not args.skip_column_lineage,
        ).run()
    except Exception as exc:
        summary = {
            "mode": "apply" if apply_changes else "dry-run",
            "status": "failed",
            "error": str(exc),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        return 1

    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if summary["counts"].get("failed", 0) else 0


if __name__ == "__main__":
    sys.exit(main())
