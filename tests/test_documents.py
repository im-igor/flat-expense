from conftest import make_expense, make_plan

PDF = b"%PDF-1.4\nprivate receipt\n"


def upload(client, headers, path, key, filename="receipt.pdf", body=PDF, content_type="application/pdf"):
    return client.post(
        path,
        params={"filename": filename, "note": "Квитанция"},
        content=body,
        headers={**headers, "Idempotency-Key": key, "Content-Type": content_type},
    )


def test_documents_are_shared_by_allowed_bot_accounts_and_can_be_deleted(client, headers, category):
    expense = make_expense(client, headers, category)
    created = upload(client, headers, f"/v1/expenses/{expense['id']}/documents", "document-1")
    assert created.status_code == 200, created.text
    document = created.json()
    assert document["filename"] == "receipt.pdf"
    assert document["previewable"] is True

    listed = client.get(f"/v1/expenses/{expense['id']}/documents", headers=headers)
    assert [item["id"] for item in listed.json()] == [document["id"]]
    content = client.get(f"/v1/documents/{document['id']}/content", headers=headers)
    assert content.content == PDF
    assert content.headers["content-type"].startswith("application/pdf")
    assert "inline" in content.headers["content-disposition"]

    other = {**headers, "X-Telegram-User-Id": "222"}
    assert client.get(f"/v1/documents/{document['id']}/content", headers=other).status_code == 200
    deleted = client.delete(
        f"/v1/documents/{document['id']}", headers={**headers, "Idempotency-Key": "document-delete"}
    )
    assert deleted.status_code == 200
    assert client.get(f"/v1/expenses/{expense['id']}/documents", headers=headers).json() == []


def test_telegram_inbox_document_can_be_attached_to_a_plan(client, headers, category):
    plan = make_plan(client, headers, category)
    created = client.post(
        "/internal/telegram/documents",
        params={"filename": "estimate.xlsx", "source": "forwarded", "note": "Смета"},
        content=b"PK\x03\x04spreadsheet",
        headers={
            "Authorization": headers["Authorization"],
            "X-Telegram-User-Id": "111",
            "Idempotency-Key": "telegram-document-1",
            "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        },
    )
    assert created.status_code == 200, created.text
    document = created.json()
    assert client.get("/v1/documents/inbox", headers=headers).json()[0]["id"] == document["id"]

    attached = client.put(
        f"/v1/documents/{document['id']}/attach",
        params={"target_type": "plan", "target_id": plan["id"]},
        headers={**headers, "Idempotency-Key": "attach-document-1"},
    )
    assert attached.status_code == 200, attached.text
    assert attached.json()["plan_id"] == plan["id"]
    assert client.get("/v1/documents/inbox", headers=headers).json() == []
    assert client.get(f"/v1/plans/{plan['id']}/documents", headers=headers).json()[0]["id"] == document["id"]


def test_document_validation_and_plan_delete_guard(client, headers, category):
    plan = make_plan(client, headers, category)
    unsupported = upload(
        client, headers, f"/v1/plans/{plan['id']}/documents", "document-unsupported", "script.exe", b"x", "application/x-msdownload"
    )
    assert unsupported.status_code == 415
    document = upload(client, headers, f"/v1/plans/{plan['id']}/documents", "document-2").json()
    blocked = client.request(
        "DELETE",
        f"/v1/plans/{plan['id']}",
        headers={**headers, "Idempotency-Key": "delete-plan-with-document"},
        json={"version": plan["version"]},
    )
    assert blocked.status_code == 409
    client.delete(
        f"/v1/documents/{document['id']}", headers={**headers, "Idempotency-Key": "delete-document-2"}
    )
    assert client.request(
        "DELETE",
        f"/v1/plans/{plan['id']}",
        headers={**headers, "Idempotency-Key": "delete-plan-after-document", "Content-Type": "application/json"},
        json={"version": plan["version"]},
    ).status_code == 200
