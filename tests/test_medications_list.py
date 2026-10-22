"""Listado paginado de medicamentos de FEAT-010 (CA-4 a CA-10)."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.db_support import fetch_scalar, unique_suffix
from tests.medications_support import (
    FORBIDDEN_KEYS,
    ITEM_KEYS,
    MEDICATIONS_PATH,
    admin_actor,
    create_medication_row,
    error_fields,
    make_catalogs,
    medico_actor,
)


async def _list(client: AsyncClient, headers: dict[str, str], **params: object) -> dict:
    response = await client.get(MEDICATIONS_PATH, headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


async def test_ca04_defaults_and_pagination_formula(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-4: sin parámetros `page=1`, `size=20` y `pages = ceil(total/20)`."""
    _, headers = await admin_actor(db_conn)
    for _ in range(3):
        await create_medication_row(db_conn)

    total_db = int(await fetch_scalar(db_conn, "SELECT count(*) FROM medications"))
    body = await _list(auth_client, headers)

    assert set(body) == {"items", "total", "page", "size", "pages"}
    assert body["page"] == 1
    assert body["size"] == 20
    assert body["total"] == total_db
    assert len(body["items"]) == min(20, total_db)
    assert body["pages"] == ((total_db + 19) // 20 if total_db else 0)
    for item in body["items"]:
        assert set(item) == ITEM_KEYS
        assert not FORBIDDEN_KEYS & set(item)


async def test_ca05_page_and_size_limits_and_out_of_range_page(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-5: límites 1..100, 422 con `field` correcto y `page=9999` vacía."""
    _, headers = await admin_actor(db_conn)

    assert (
        await auth_client.get(MEDICATIONS_PATH, headers=headers, params={"size": 1})
    ).status_code == 200
    assert (
        await auth_client.get(MEDICATIONS_PATH, headers=headers, params={"size": 100})
    ).status_code == 200

    for params, field in (
        ({"size": 0}, "size"),
        ({"size": 101}, "size"),
        ({"size": -1}, "size"),
        ({"page": 0}, "page"),
        ({"page": -1}, "page"),
    ):
        response = await auth_client.get(MEDICATIONS_PATH, headers=headers, params=params)
        assert response.status_code == 422, (params, response.text)
        assert error_fields(response) == {field}

    first = await _list(auth_client, headers, page=1)
    far = await _list(auth_client, headers, page=9999)

    assert far["items"] == []
    assert far["page"] == 9999
    assert far["size"] == 20
    assert far["total"] == first["total"]
    assert far["pages"] == first["pages"]


async def test_ca06_sort_catalog_direction_tiebreak_and_invalids(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-6: los ocho campos en ambos sentidos, desempate e inválidos."""
    _, headers = await admin_actor(db_conn)
    drug_type_id, brand_id, location_id = await make_catalogs(db_conn)
    tag = unique_suffix()
    prefix = f"zm{tag}"
    letters = "abcde"
    states = [True, False, True, False, True]
    for letter, is_active in zip(letters, states, strict=True):
        await create_medication_row(
            db_conn,
            f"{prefix}{letter}",
            drug_type_id=drug_type_id,
            brand_id=brand_id,
            location_id=location_id,
            dose=f"{letter}-dose-{tag}",
            is_active=is_active,
        )

    def only(body: dict, field: str) -> list:
        return [
            item[field] for item in body["items"] if str(item["description"]).startswith(prefix)
        ]

    asc = await _list(auth_client, headers, search=prefix, sort="description", size=100)
    assert only(asc, "description") == sorted(only(asc, "description"))
    desc = await _list(auth_client, headers, search=prefix, sort="-description", size=100)
    assert only(desc, "description") == sorted(only(desc, "description"), reverse=True)

    for field in ("dose", "drug_type_id", "brand_id", "location_id"):
        ordered = await _list(auth_client, headers, search=prefix, sort=field, size=100)
        assert only(ordered, field) == sorted(only(ordered, field)), field
        ordered_desc = await _list(auth_client, headers, search=prefix, sort=f"-{field}", size=100)
        assert only(ordered_desc, field) == sorted(only(ordered_desc, field), reverse=True), field

    for sort in ("created_at", "-created_at", "is_active", "-is_active", "id", "-id"):
        body = await _list(auth_client, headers, search=prefix, sort=sort, size=100)
        assert len(only(body, "description")) == len(letters), sort

    is_asc = await _list(auth_client, headers, search=prefix, sort="is_active", size=100)
    flags = [
        item["is_active"] for item in is_asc["items"] if str(item["description"]).startswith(prefix)
    ]
    assert flags == sorted(flags)
    is_desc = await _list(auth_client, headers, search=prefix, sort="-is_active", size=100)
    flags_desc = [
        item["is_active"]
        for item in is_desc["items"]
        if str(item["description"]).startswith(prefix)
    ]
    assert flags_desc == sorted(flags_desc, reverse=True)

    created = await _list(auth_client, headers, search=prefix, sort="created_at", size=100)
    created_ids = [
        item["id"] for item in created["items"] if str(item["description"]).startswith(prefix)
    ]
    assert created_ids == sorted(created_ids)
    created_desc = await _list(auth_client, headers, search=prefix, sort="-created_at", size=100)
    created_desc_ids = [
        item["id"] for item in created_desc["items"] if str(item["description"]).startswith(prefix)
    ]
    assert created_desc_ids == sorted(created_desc_ids)

    groups: dict[bool, list[int]] = {}
    for item in is_asc["items"]:
        if str(item["description"]).startswith(prefix):
            groups.setdefault(item["is_active"], []).append(item["id"])
    assert groups
    for ids in groups.values():
        assert ids == sorted(ids)

    default = await _list(auth_client, headers, search=prefix, size=100)
    assert only(default, "description") == sorted(only(default, "description"))

    for invalid in ("password_hash", "descriptionx", "dose,dose", ""):
        response = await auth_client.get(
            MEDICATIONS_PATH, headers=headers, params={"sort": invalid}
        )
        assert error_fields(response) == {"sort"}, (invalid, response.text)


async def test_ca07_search_covers_description_and_dose_escapes_wildcards(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-7: `search` parcial y case-insensitive en `description` o `dose`, comodines literales."""
    _, headers = await admin_actor(db_conn)
    drug_type_id, brand_id, location_id = await make_catalogs(db_conn)
    tag = unique_suffix()
    target_id = await create_medication_row(
        db_conn,
        f"Jarabe Central {tag}",
        drug_type_id=drug_type_id,
        brand_id=brand_id,
        location_id=location_id,
        dose=f"Dosis {tag}",
    )

    description_hit = await _list(auth_client, headers, search=f"RABE CENTRAL {tag}".lower())
    assert target_id in {item["id"] for item in description_hit["items"]}

    dose_hit = await _list(auth_client, headers, search=f"dosis {tag}")
    assert target_id in {item["id"] for item in dose_hit["items"]}

    dose_only_id = await create_medication_row(
        db_conn,
        f"Otra {tag}",
        drug_type_id=drug_type_id,
        brand_id=brand_id,
        location_id=location_id,
        dose=f"750 mg {tag}",
    )
    dose_only = await _list(auth_client, headers, search=f"750 mg {tag}")
    assert dose_only_id in {item["id"] for item in dose_only["items"]}
    assert target_id not in {item["id"] for item in dose_only["items"]}

    percent_tag = unique_suffix()
    percent_id = await create_medication_row(
        db_conn,
        f"Med %50 {percent_tag}",
        drug_type_id=drug_type_id,
        brand_id=brand_id,
        location_id=location_id,
        dose=f"% x {percent_tag}",
    )
    control_id = await create_medication_row(
        db_conn,
        f"Med 50 {percent_tag}",
        drug_type_id=drug_type_id,
        brand_id=brand_id,
        location_id=location_id,
        dose=f"x {percent_tag}",
    )
    percent_body = await _list(auth_client, headers, search=f"%50 {percent_tag}")
    percent_ids = {item["id"] for item in percent_body["items"]}
    assert percent_id in percent_ids
    assert control_id not in percent_ids

    underscore_tag = unique_suffix()
    underscore_id = await create_medication_row(
        db_conn,
        f"Med_sub {underscore_tag}",
        drug_type_id=drug_type_id,
        brand_id=brand_id,
        location_id=location_id,
    )
    underscore_body = await _list(auth_client, headers, search=f"d_sub {underscore_tag}")
    assert underscore_id in {item["id"] for item in underscore_body["items"]}

    backslash_tag = unique_suffix()
    backslash_id = await create_medication_row(
        db_conn,
        f"Med\\base {backslash_tag}",
        drug_type_id=drug_type_id,
        brand_id=brand_id,
        location_id=location_id,
    )
    backslash_body = await _list(auth_client, headers, search=f"d\\base {backslash_tag}")
    assert backslash_id in {item["id"] for item in backslash_body["items"]}

    plain = await _list(auth_client, headers)
    blank = await _list(auth_client, headers, search="   ")
    assert blank["total"] == plain["total"]

    long_response = await auth_client.get(
        MEDICATIONS_PATH, headers=headers, params={"search": "x" * 101}
    )
    assert error_fields(long_response) == {"search"}


async def test_ca08_filters_by_state_and_fks_combined_with_search(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-8: `is_active` y los tres FK filtran exactos y se combinan con `search`."""
    _, headers = await admin_actor(db_conn)
    drug_a, brand_a, loc_a = await make_catalogs(db_conn, suffix=unique_suffix())
    drug_b, brand_b, loc_b = await make_catalogs(db_conn, suffix=unique_suffix())
    tag = unique_suffix()
    active_id = await create_medication_row(
        db_conn,
        f"Filtro {tag} A",
        drug_type_id=drug_a,
        brand_id=brand_a,
        location_id=loc_a,
        is_active=True,
    )
    inactive_id = await create_medication_row(
        db_conn,
        f"Filtro {tag} B",
        drug_type_id=drug_b,
        brand_id=brand_b,
        location_id=loc_b,
        is_active=False,
    )

    active = await _list(auth_client, headers, search=tag, is_active=True)
    assert {item["id"] for item in active["items"]} == {active_id}
    assert all(item["is_active"] is True for item in active["items"])

    inactive = await _list(auth_client, headers, search=tag, is_active=False)
    assert {item["id"] for item in inactive["items"]} == {inactive_id}
    assert all(item["is_active"] is False for item in inactive["items"])

    both = await _list(auth_client, headers, search=tag)
    assert {item["id"] for item in both["items"]} == {active_id, inactive_id}

    by_drug = await _list(auth_client, headers, search=tag, drug_type_id=drug_a)
    assert {item["id"] for item in by_drug["items"]} == {active_id}

    by_brand = await _list(auth_client, headers, search=tag, brand_id=brand_b)
    assert {item["id"] for item in by_brand["items"]} == {inactive_id}

    by_location = await _list(auth_client, headers, search=tag, location_id=loc_a)
    assert {item["id"] for item in by_location["items"]} == {active_id}

    combined = await _list(
        auth_client,
        headers,
        search=tag,
        drug_type_id=drug_a,
        brand_id=brand_a,
        location_id=loc_a,
        is_active=True,
    )
    assert {item["id"] for item in combined["items"]} == {active_id}

    mismatch = await _list(auth_client, headers, search=tag, drug_type_id=drug_a, brand_id=brand_b)
    assert mismatch["items"] == []

    for param in ("drug_type_id", "brand_id", "location_id"):
        for value in ("0", "-1", "abc"):
            response = await auth_client.get(
                MEDICATIONS_PATH, headers=headers, params={param: value}
            )
            assert response.status_code == 422, (param, value, response.text)
            assert error_fields(response) == {param}


async def test_ca09_empty_search_results_have_zero_pages(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-9: sin coincidencias ⇒ `items == []`, `total == 0` y `pages == 0`."""
    _, headers = await admin_actor(db_conn)
    body = await _list(auth_client, headers, search=f"sincoincidencias{unique_suffix()}", page=2)

    assert body["items"] == []
    assert body["total"] == 0
    assert body["pages"] == 0
    assert body["page"] == 2
    assert body["size"] == 20


async def test_ca10_items_never_leak_timestamps_or_nested(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-10: cada `item` tiene exactamente las siete claves planas de D-4."""
    _, admin_headers = await admin_actor(db_conn)
    _, medico_headers = await medico_actor(db_conn)
    await create_medication_row(db_conn)

    for headers in (admin_headers, medico_headers):
        body = await _list(auth_client, headers)
        assert body["items"], headers
        for item in body["items"]:
            assert set(item) == ITEM_KEYS
