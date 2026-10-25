import json
import socket
import sys
from uuid import UUID, uuid4

import pytest

from repositories.supplier_identity_repository import (
    CONFIRMED_IDENTIFIER_COLLISION,
    SupplierIdentityRepository,
    SupplierIdentityRepositoryError,
)
from schemas.supplier_identity_schema import SupplierIdentifier, SupplierIdentity


def ident(id_type="SIRET", value="12345678900012", country="FR", confirmed=True):
    return SupplierIdentifier(
        identifier_type=id_type,
        country=country,
        raw_value=value,
        normalized_value=value,
        human_confirmed=confirmed,
    )


def supplier(name="Acme", identifiers=(), **kwargs):
    return SupplierIdentity(legal_name=name, identifiers=list(identifiers), **kwargs)


@pytest.fixture
def repo(tmp_path):
    return SupplierIdentityRepository(tmp_path / "data" / "suppliers.json")


def test_create_and_get(repo):
    created = repo.create(supplier())
    assert repo.get(created.supplier_id) == created
    assert repo.get(str(created.supplier_id)) == created
    assert repo.get(uuid4()) is None


def test_uuid_is_storage_key(repo):
    created = repo.create(supplier())
    data = json.loads(repo.path.read_text(encoding="utf-8"))
    assert list(data["suppliers"]) == [str(created.supplier_id)]
    assert isinstance(created.supplier_id, UUID)


def test_duplicate_create_refused(repo):
    created = repo.create(supplier())
    with pytest.raises(SupplierIdentityRepositoryError):
        repo.create(created)


def test_update(repo):
    created = repo.create(supplier())
    updated = repo.update(created.model_copy(update={"legal_name": "Acme SAS"}))
    assert repo.get(created.supplier_id).legal_name == "Acme SAS"
    assert updated.supplier_id == created.supplier_id


def test_update_unknown_id_refused(repo):
    repo.create(supplier())
    with pytest.raises(SupplierIdentityRepositoryError) as exc:
        repo.update(supplier("Other"))
    assert exc.value.code == "SUPPLIER_NOT_FOUND"


def test_update_validates_full_object(repo):
    created = repo.create(supplier())
    invalid = created.model_copy(update={"legal_name": "   "})
    with pytest.raises(ValueError):
        repo.update(invalid)
    assert repo.get(created.supplier_id).legal_name == "Acme"


def test_deactivate_is_reversible_and_not_physical(repo):
    created = repo.create(supplier(identifiers=[ident()]))
    repo.deactivate(created.supplier_id)
    assert repo.get(created.supplier_id).active is False
    assert repo.list_active() == []
    assert repo.find_by_confirmed_identifier("SIRET", "12345678900012", "FR") is None
    repo.update(repo.get(created.supplier_id).model_copy(update={"active": True}))
    assert [s.supplier_id for s in repo.list_active()] == [created.supplier_id]


def test_list_active(repo):
    a = repo.create(supplier("A"))
    b = repo.create(supplier("B"))
    repo.deactivate(b.supplier_id)
    assert [s.supplier_id for s in repo.list_active()] == [a.supplier_id]


def test_find_confirmed_siret(repo):
    created = repo.create(supplier(identifiers=[ident()]))
    found = repo.find_by_confirmed_identifier("siret", "12345678900012", "fr")
    assert found.supplier_id == created.supplier_id


def test_find_foreign_vat(repo):
    created = repo.create(supplier("Gmbh", [ident("VAT", "DE123456789", "DE")]))
    assert repo.find_by_confirmed_identifier("VAT", "DE123456789", "DE").supplier_id == created.supplier_id
    assert repo.find_by_confirmed_identifier("VAT", "DE123456789", "FR") is None


def test_custom_identifier_type(repo):
    created = repo.create(supplier("Brazil", [ident("CNPJ", "12345678000195", "BR")]))
    assert repo.find_by_confirmed_identifier("CNPJ", "12345678000195", "BR").supplier_id == created.supplier_id


def test_unconfirmed_identifier_not_indexed(repo):
    repo.create(supplier(identifiers=[ident(confirmed=False)]))
    assert repo.find_by_confirmed_identifier("SIRET", "12345678900012", "FR") is None


def test_empty_normalized_value_not_indexed(repo):
    raw_only = SupplierIdentifier(
        identifier_type="SIRET", country="FR", raw_value="123 456", human_confirmed=True
    )
    repo.create(supplier(identifiers=[raw_only]))
    assert repo.find_by_confirmed_identifier("SIRET", "123 456", "FR") is None


def test_no_country_matches_only_countryless_identifier(repo):
    created = repo.create(supplier(identifiers=[ident("TAXID", "X1", None)]))
    assert repo.find_by_confirmed_identifier("TAXID", "X1").supplier_id == created.supplier_id
    assert repo.find_by_confirmed_identifier("TAXID", "X1", "FR") is None


def test_collision_refused_without_writing(repo):
    repo.create(supplier("A", [ident()]))
    before = repo.path.read_text(encoding="utf-8")
    with pytest.raises(SupplierIdentityRepositoryError) as exc:
        repo.create(supplier("B", [ident()]))
    assert exc.value.code == CONFIRMED_IDENTIFIER_COLLISION
    assert repo.path.read_text(encoding="utf-8") == before


def test_collision_on_update_refused(repo):
    repo.create(supplier("A", [ident()]))
    b = repo.create(supplier("B", [ident(value="99999999900011")]))
    with pytest.raises(SupplierIdentityRepositoryError) as exc:
        repo.update(b.model_copy(update={"identifiers": [ident()]}))
    assert exc.value.code == CONFIRMED_IDENTIFIER_COLLISION


def test_collision_on_reactivation_refused(repo):
    a = repo.create(supplier("A", [ident()]))
    repo.deactivate(a.supplier_id)
    repo.create(supplier("B", [ident()]))
    with pytest.raises(SupplierIdentityRepositoryError) as exc:
        repo.update(repo.get(a.supplier_id).model_copy(update={"active": True}))
    assert exc.value.code == CONFIRMED_IDENTIFIER_COLLISION


def test_inactive_supplier_does_not_collide(repo):
    a = repo.create(supplier("A", [ident()]))
    repo.deactivate(a.supplier_id)
    b = repo.create(supplier("B", [ident()]))
    assert repo.find_by_confirmed_identifier("SIRET", "12345678900012", "FR").supplier_id == b.supplier_id


def test_unconfirmed_duplicates_do_not_collide(repo):
    repo.create(supplier("A", [ident(confirmed=False)]))
    repo.create(supplier("B", [ident(confirmed=False)]))
    repo.create(supplier("C", [ident()]))


def test_same_value_different_types_no_collision(repo):
    repo.create(supplier("A", [ident("SIRET", "123456789", "FR")]))
    repo.create(supplier("B", [ident("VAT", "123456789", "FR")]))


def test_same_type_value_different_countries_no_collision(repo):
    repo.create(supplier("A", [ident("VAT", "123456789", "FR")]))
    repo.create(supplier("B", [ident("VAT", "123456789", "BE")]))


def test_suggest_by_legal_name_case_insensitive(repo):
    created = repo.create(supplier("Acme SAS"))
    assert [s.supplier_id for s in repo.suggest_by_name("  acme sas ")] == [created.supplier_id]


def test_suggest_by_printed_name_and_alias(repo):
    created = repo.create(supplier("Legal", printed_names=["Printed Co"], aliases=["Trade Alias"]))
    assert repo.suggest_by_name("PRINTED CO")[0].supplier_id == created.supplier_id
    assert repo.suggest_by_name("trade alias")[0].supplier_id == created.supplier_id


def test_suggest_no_match_is_empty(repo):
    repo.create(supplier("Acme"))
    assert repo.suggest_by_name("Nothing") == []
    assert repo.suggest_by_name("   ") == []


def test_suggest_never_merges_or_modifies(repo):
    repo.create(supplier("Acme", [ident()]))
    repo.create(supplier("ACME"))
    before = repo.path.read_bytes()
    assert len(repo.suggest_by_name("acme")) == 2
    assert repo.path.read_bytes() == before
    assert len(repo.list_active()) == 2


def test_multiple_candidates_same_name(repo):
    repo.create(supplier("Dupont"))
    repo.create(supplier("Dupont", aliases=["Other"]))
    assert len(repo.suggest_by_name("dupont")) == 2


def test_atomic_save_and_backup(repo):
    first = repo.create(supplier("A"))
    assert not repo.path.with_name("suppliers.json.bak").exists()
    repo.create(supplier("B"))
    backup = repo.path.with_name("suppliers.json.bak")
    assert backup.exists()
    assert list(json.loads(backup.read_text(encoding="utf-8"))["suppliers"]) == [str(first.supplier_id)]
    assert not repo.path.with_name("suppliers.json.tmp").exists()


def test_reload_from_disk(repo):
    created = repo.create(supplier("A", [ident()], aliases=["Alias"]))
    reloaded = SupplierIdentityRepository(repo.path)
    assert reloaded.get(created.supplier_id) == created
    assert reloaded.find_by_confirmed_identifier("SIRET", "12345678900012", "FR") == created


def test_invalid_json_refused_without_overwrite(repo):
    repo.path.parent.mkdir(parents=True)
    repo.path.write_text("{not json", encoding="utf-8")
    for action in (lambda: repo.create(supplier()), repo.list_active, lambda: repo.suggest_by_name("a")):
        with pytest.raises(SupplierIdentityRepositoryError) as exc:
            action()
        assert exc.value.code == "INVALID_STORAGE"
    assert repo.path.read_text(encoding="utf-8") == "{not json"
    assert not repo.path.with_name("suppliers.json.bak").exists()


def test_invalid_record_refused(repo):
    repo.path.parent.mkdir(parents=True)
    repo.path.write_text(json.dumps({"version": 1, "suppliers": {str(uuid4()): {"x": 1}}}), encoding="utf-8")
    with pytest.raises(SupplierIdentityRepositoryError):
        repo.list_active()


def test_no_network_access(repo, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network access")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    created = repo.create(supplier("A", [ident()]))
    repo.update(created)
    repo.suggest_by_name("A")
    repo.find_by_confirmed_identifier("SIRET", "12345678900012", "FR")


def test_no_amount_computation(repo):
    created = repo.create(supplier())
    dumped = repo.get(created.supplier_id).model_dump()
    assert not {"amount", "vat", "total_ht", "total_ttc"} & set(dumped)


def test_legacy_repository_never_imported():
    import repositories.supplier_identity_repository as module

    source = open(module.__file__, encoding="utf-8").read()
    assert "supplier_repository" not in source
    assert "services.supplier_repository" not in sys.modules
