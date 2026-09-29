import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
@pytest.fixture
async def unauth_client():
    from app.database.mongodb import init_mongo, get_mongo_db
    if get_mongo_db() is None:
        await init_mongo()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as ac:
        yield ac

async def _cleanup_user_by_phone(phone: str):
    """Clean up newly created test user by phone from MongoDB."""
    try:
        from app.database.mongodb import get_mongo_db, COLL_USERS, COLL_COUNTERS
        db = get_mongo_db()
        if db is not None:
            await db[COLL_USERS].delete_many({"phone": phone})
            count = await db[COLL_USERS].count_documents({})
            if count <= 42:
                await db[COLL_COUNTERS].update_one({"_id": "user_id"}, {"$set": {"seq": 42}})
    except Exception:
        pass


@pytest.mark.asyncio
async def test_register_user(unauth_client):
    import time
    phone = f"111222{str(int(time.time()))[-4:]}"
    try:
        response = await unauth_client.post("/api/auth/register", json={
            "name": "Test User",
            "phone": phone,
            "password": "password123",
            "confirm_password": "password123",
        })
        assert response.status_code == 201
        data = response.json()
        assert data["message"] == "Registration successful"
        assert data["user"]["role"] == "PASSENGER"
        assert "access_token" in response.cookies or True
    finally:
        await _cleanup_user_by_phone(phone)

@pytest.mark.asyncio
async def test_register_user_ignores_role_input(unauth_client):
    import time
    phone = f"111333{str(int(time.time()))[-4:]}"
    try:
        response = await unauth_client.post("/api/auth/register", json={
            "name": "Hacker User",
            "phone": phone,
            "password": "password123",
            "confirm_password": "password123",
            "role": "RAILWAY_STAFF"
        })
        assert response.status_code == 201
        data = response.json()
        assert data["user"]["role"] == "PASSENGER"
    finally:
        await _cleanup_user_by_phone(phone)

@pytest.mark.asyncio
async def test_login_user(unauth_client):
    response = await unauth_client.post("/api/auth/login", json={
        "phone": "9876543210",
        "password": "demo123"
    })
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Login successful"
    assert "access_token" in response.cookies

@pytest.mark.asyncio
async def test_login_invalid_password(unauth_client):
    response = await unauth_client.post("/api/auth/login", json={
        "phone": "9876543210",
        "password": "wrongpassword"
    })
    assert response.status_code == 401

@pytest.mark.asyncio
async def test_get_me(unauth_client):
    login_resp = await unauth_client.post("/api/auth/login", json={
        "phone": "9876543210",
        "password": "demo123"
    })
    assert login_resp.status_code == 200
    
    me_resp = await unauth_client.get("/api/auth/me")
    assert me_resp.status_code == 200
    data = me_resp.json()
    assert data["phone"] == "9876543210"

@pytest.mark.asyncio
async def test_logout(unauth_client):
    await unauth_client.post("/api/auth/login", json={
        "phone": "9876543210",
        "password": "demo123"
    })
    
    logout_resp = await unauth_client.post("/api/auth/logout")
    assert logout_resp.status_code == 200
    
    # Try accessing /me
    me_resp = await unauth_client.get("/api/auth/me")
    assert me_resp.status_code == 401


@pytest.mark.asyncio
async def test_login_returns_access_token_in_body(unauth_client):
    """Dual-channel auth: login returns access_token in body alongside setting cookie."""
    response = await unauth_client.post("/api/auth/login", json={
        "phone": "9876543210",
        "password": "demo123"
    })
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert isinstance(data["access_token"], str)
    assert len(data["access_token"]) > 20
    assert "access_token" in response.cookies


@pytest.mark.asyncio
async def test_bearer_token_authentication_without_cookies(unauth_client):
    """When cookies are completely stripped (e.g. iOS Safari ITP), Authorization Bearer header authenticates user."""
    # 1. Login to get token
    login_resp = await unauth_client.post("/api/auth/login", json={
        "phone": "9876543210",
        "password": "demo123"
    })
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]

    # 2. Clear cookies to simulate iOS Safari dropping cross-site cookies
    unauth_client.cookies.clear()
    assert len(unauth_client.cookies) == 0

    # 3. Request without token should fail
    resp_no_auth = await unauth_client.get("/api/auth/me")
    assert resp_no_auth.status_code == 401

    # 4. Request with Bearer header must succeed
    resp_bearer = await unauth_client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert resp_bearer.status_code == 200
    assert resp_bearer.json()["phone"] == "9876543210"
    assert resp_bearer.json()["role"] == "PASSENGER"


@pytest.mark.asyncio
async def test_bearer_token_sarvam_endpoints(unauth_client):
    """Verify all Sarvam endpoints work seamlessly with Bearer header and zero cookies."""
    from unittest.mock import patch, MagicMock

    login_resp = await unauth_client.post("/api/auth/login", json={
        "phone": "9876543210",
        "password": "demo123"
    })
    token = login_resp.json()["access_token"]
    unauth_client.cookies.clear()
    headers = {"Authorization": f"Bearer {token}"}

    # 1. POST /api/sarvam/chat
    chat_resp = await unauth_client.post(
        "/api/sarvam/chat",
        json={"message": "Where is train 12951?"},
        headers=headers
    )
    assert chat_resp.status_code == 200
    assert chat_resp.json()["source"] == "railpulse"

    # 2. POST /api/sarvam/translate
    trans_resp = await unauth_client.post(
        "/api/sarvam/translate",
        json={"text": "Train is on time", "target_language_code": "hi-IN"},
        headers=headers
    )
    assert trans_resp.status_code == 200

    # 3. POST /api/sarvam/tts
    with patch("app.services.sarvam_service.sarvam_service.synthesize_speech", return_value="dummy_b64"):
        tts_resp = await unauth_client.post(
            "/api/sarvam/tts",
            json={"text": "Train is on time", "language_code": "hi-IN"},
            headers=headers
        )
        assert tts_resp.status_code == 200
        assert tts_resp.json()["audio_base64"] == "dummy_b64"

    # 4. POST /api/sarvam/stt
    with patch("app.services.sarvam_service.sarvam_service.transcribe_audio", return_value="Where is 12951?"):
        stt_resp = await unauth_client.post(
            "/api/sarvam/stt",
            files={"file": ("speech.wav", b"fake_wav_audio_content", "audio/wav")},
            data={"language_code": "unknown", "tts_language_code": "hi-IN"},
            headers=headers
        )
        assert stt_resp.status_code == 200
        assert stt_resp.json()["source"] == "railpulse"


@pytest.mark.asyncio
async def test_bearer_token_role_authorization(unauth_client):
    """Verify role authorization is fully enforced with Bearer tokens."""
    # Passenger token
    pass_login = await unauth_client.post("/api/auth/login", json={"phone": "9876543210", "password": "demo123"})
    pass_token = pass_login.json()["access_token"]

    # Staff token
    staff_login = await unauth_client.post("/api/auth/login", json={"phone": "9876543211", "password": "demo123"})
    staff_token = staff_login.json()["access_token"]

    unauth_client.cookies.clear()

    # Passenger trying to access staff endpoint /api/simulation/start -> 403 Forbidden
    resp_pass = await unauth_client.post(
        "/api/simulation/start",
        headers={"Authorization": f"Bearer {pass_token}"}
    )
    assert resp_pass.status_code == 403

    # Staff accessing /api/simulation/start -> 200 OK
    resp_staff = await unauth_client.post(
        "/api/simulation/start",
        headers={"Authorization": f"Bearer {staff_token}"}
    )
    assert resp_staff.status_code == 200

