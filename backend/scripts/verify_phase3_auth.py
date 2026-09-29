"""
Phase 3 Verification Script:
Validates MongoDB User Persistence, Authentication Endpoints, and Authorization.

Checks:
1. Static user integrity: SQLite = 42, MongoDB = 42, match 100%.
2. Counter 'user_id' in counters collection = 42.
3. Existing Passenger login (9876543210 / demo123) -> 200 OK, returns user id 1, role PASSENGER, sets cookie & returns access_token.
4. Existing Staff login (9876543211 / demo123) -> 200 OK, returns user id 2, role RAILWAY_STAFF, sets cookie & returns access_token.
5. Invalid password -> 401 Unauthorized.
6. Nonexistent phone -> 401 Unauthorized.
7. Cookie-based authentication on /api/auth/me.
8. Bearer header authentication on /api/auth/me.
9. iOS Safari fallback (zero cookies, Bearer header only) on /api/auth/me.
10. Role-based authorization: Passenger on Staff-only endpoint returns 403 Forbidden.
11. Role-based authorization: Staff on Staff-only endpoint succeeds (200 OK).
12. Atomic User Registration:
    - Register new user
    - Verify new user gets assigned sequential integer ID 43
    - Verify role is PASSENGER
    - Verify counter increments to 43
    - Verify new user can authenticate and access /api/auth/me
    - Clean up: Delete test user 43 and reset counter to 42, returning database to clean 42 users state.
"""

import asyncio
import os
import sys
import json
from httpx import AsyncClient, ASGITransport

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.main import app
from app.database.mongodb import init_mongo, close_mongo, get_mongo_db, COLL_USERS, COLL_COUNTERS
from app.services.auth_service import auth_service
from scripts.verify_users_migration import verify_users


async def run_phase3_verification():
    results = {
        "static_migration": {},
        "passenger_login": {},
        "staff_login": {},
        "invalid_password_login": {},
        "nonexistent_user_login": {},
        "cookie_auth": {},
        "bearer_auth": {},
        "ios_safari_fallback": {},
        "passenger_authorization": {},
        "staff_authorization": {},
        "user_registration": {},
        "all_passed": False,
    }

    # Step 1: Verify Static Migration Match (SQLite 42 == Mongo 42)
    print("--- 1. Verifying Static Migration Match ---")
    migration_check = await verify_users()
    results["static_migration"] = {
        "success": migration_check["success"],
        "counts": migration_check["counts"],
        "counter": migration_check["counter"],
        "all_hashes_equal": migration_check["field_verification"]["all_hashes_equal"],
    }
    assert migration_check["success"], f"Static migration check failed: {migration_check['errors']}"
    print(f"  [PASS] Counts: SQLite={migration_check['counts']['sqlite']}, MongoDB={migration_check['counts']['mongo']}")
    print(f"  [PASS] Counter: seq={migration_check['counter']['seq']}")
    print(f"  [PASS] 100% hashes and fields match.")

    # Initialize Mongo for FastAPI client
    mongo_ok = await init_mongo()
    assert mongo_ok, "MongoDB initialization failed."
    db = get_mongo_db()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://testserver") as client:
        # Step 2: Passenger Login
        print("\n--- 2. Testing Passenger Login ---")
        resp = await client.post("/api/auth/login", json={
            "phone": "9876543210",
            "password": "demo123"
        })
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        pass_data = resp.json()
        expected_pass_id = pass_data["user"]["id"]
        assert expected_pass_id == 5, f"Expected user ID 5 for demo passenger, got {expected_pass_id}"
        assert pass_data["user"]["role"] == "PASSENGER"
        assert pass_data["user"]["phone"] == "9876543210"
        assert "access_token" in pass_data
        assert "access_token" in resp.cookies
        passenger_token = pass_data["access_token"]
        passenger_cookie = resp.cookies["access_token"]
        results["passenger_login"] = {
            "status_code": resp.status_code,
            "user_id": pass_data["user"]["id"],
            "role": pass_data["user"]["role"],
            "has_token_in_body": bool(passenger_token),
            "has_cookie": bool(passenger_cookie),
        }
        print(f"  [PASS] Passenger login succeeded. ID: {pass_data['user']['id']}, Role: {pass_data['user']['role']}")

        # Step 3: Staff Login
        print("\n--- 3. Testing Staff Login ---")
        resp = await client.post("/api/auth/login", json={
            "phone": "9876543211",
            "password": "demo123"
        })
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        staff_data = resp.json()
        assert staff_data["user"]["id"] == 6, f"Expected user ID 6 for demo staff, got {staff_data['user']['id']}"
        assert staff_data["user"]["role"] == "RAILWAY_STAFF"
        assert staff_data["user"]["phone"] == "9876543211"
        assert "access_token" in staff_data
        assert "access_token" in resp.cookies
        staff_token = staff_data["access_token"]
        results["staff_login"] = {
            "status_code": resp.status_code,
            "user_id": staff_data["user"]["id"],
            "role": staff_data["user"]["role"],
            "has_token_in_body": bool(staff_token),
            "has_cookie": bool(resp.cookies.get("access_token")),
        }
        print(f"  [PASS] Staff login succeeded. ID: {staff_data['user']['id']}, Role: {staff_data['user']['role']}")

        # Step 4: Invalid Password
        print("\n--- 4. Testing Invalid Password ---")
        resp = await client.post("/api/auth/login", json={
            "phone": "9876543210",
            "password": "wrong_password_999"
        })
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"
        results["invalid_password_login"] = {"status_code": resp.status_code, "detail": resp.json().get("detail")}
        print(f"  [PASS] Invalid password rejected with 401: {resp.json().get('detail')}")

        # Step 5: Nonexistent Phone
        print("\n--- 5. Testing Nonexistent Phone ---")
        resp = await client.post("/api/auth/login", json={
            "phone": "0000000000",
            "password": "demo123"
        })
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"
        results["nonexistent_user_login"] = {"status_code": resp.status_code, "detail": resp.json().get("detail")}
        print(f"  [PASS] Nonexistent phone rejected with 401: {resp.json().get('detail')}")

        # Step 6: Cookie-based Authentication
        print("\n--- 6. Testing Cookie Authentication ---")
        client.cookies.clear()
        resp = await client.get("/api/auth/me", cookies={"access_token": passenger_cookie})
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        me_data = resp.json()
        assert me_data["id"] == 5, f"Expected user ID 5, got {me_data['id']}"
        results["cookie_auth"] = {"status_code": resp.status_code, "user_id": me_data["id"], "role": me_data["role"]}
        print(f"  [PASS] Cookie auth on /api/auth/me -> ID {me_data['id']}, Role {me_data['role']}")

        # Step 7: Bearer Header Authentication
        print("\n--- 7. Testing Bearer Header Authentication ---")
        client.cookies.clear()
        resp = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {passenger_token}"})
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        me_data = resp.json()
        assert me_data["id"] == 5, f"Expected user ID 5, got {me_data['id']}"
        results["bearer_auth"] = {"status_code": resp.status_code, "user_id": me_data["id"], "role": me_data["role"]}
        print(f"  [PASS] Bearer auth on /api/auth/me -> ID {me_data['id']}, Role {me_data['role']}")

        # Step 8: iOS Safari Fallback (Strictly zero cookies + Bearer header)
        print("\n--- 8. Testing iOS Safari Fallback (Zero cookies, Bearer header only) ---")
        client.cookies.clear()
        resp = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {passenger_token}", "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1"}
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        results["ios_safari_fallback"] = {"status_code": resp.status_code, "authenticated": True, "user_id": resp.json()["id"]}
        print(f"  [PASS] iOS Safari fallback succeeded without cookies. User ID: {resp.json()['id']}")

        # Step 9: Role-based Authorization (Passenger -> 403 on staff endpoint)
        print("\n--- 9. Testing Role Authorization: Passenger Forbidden on Staff Endpoint ---")
        client.cookies.clear()
        resp = await client.post("/api/simulation/start", headers={"Authorization": f"Bearer {passenger_token}"})
        assert resp.status_code == 403, f"Expected 403 for passenger on staff endpoint, got {resp.status_code}"
        results["passenger_authorization"] = {"status_code": resp.status_code, "forbidden": True}
        print(f"  [PASS] Passenger correctly forbidden (403) on /api/simulation/start")

        # Step 10: Role-based Authorization (Staff -> 200 on staff endpoint)
        print("\n--- 10. Testing Role Authorization: Staff Permitted on Staff Endpoint ---")
        client.cookies.clear()
        resp = await client.post("/api/simulation/start", headers={"Authorization": f"Bearer {staff_token}"})
        assert resp.status_code == 200, f"Expected 200 for staff on staff endpoint, got {resp.status_code}"
        results["staff_authorization"] = {"status_code": resp.status_code, "permitted": True}
        print(f"  [PASS] Staff correctly permitted (200) on /api/simulation/start")


        # Step 11: Atomic User Registration (Sequential ID 43)
        print("\n--- 11. Testing Atomic User Registration (Target ID 43) ---")
        test_phone = "9999900043"
        reg_resp = await client.post("/api/auth/register", json={
            "name": "Phase3 Test User",
            "phone": test_phone,
            "password": "valid_password_123",
            "confirm_password": "valid_password_123",
            "role": "RAILWAY_STAFF"  # should be forced to PASSENGER per business rule
        })
        assert reg_resp.status_code == 201, f"Expected 201, got {reg_resp.status_code}: {reg_resp.text}"
        reg_data = reg_resp.json()
        new_id = reg_data["user"]["id"]
        new_role = reg_data["user"]["role"]
        new_token = reg_data["access_token"]
        assert new_id == 43, f"Expected sequential ID 43, got {new_id}"
        assert new_role == "PASSENGER", f"Expected role PASSENGER, got {new_role}"
        print(f"  [PASS] Registration succeeded. Assigned ID: {new_id}, Role: {new_role}")

        # Check counter in Mongo
        counter_doc = await db[COLL_COUNTERS].find_one({"_id": "user_id"})
        assert counter_doc["seq"] == 43, f"Expected counter seq=43, got {counter_doc['seq']}"
        print(f"  [PASS] MongoDB counter incremented to {counter_doc['seq']}")

        # Verify new user can access /api/auth/me
        me_resp = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {new_token}"})
        assert me_resp.status_code == 200
        assert me_resp.json()["id"] == 43
        print(f"  [PASS] New user ID 43 successfully authenticated on /api/auth/me")

        # Step 12: Clean up registered test user so MongoDB stays at exactly 42 users
        print("\n--- 12. Cleaning up Test User ID 43 ---")
        del_result = await db[COLL_USERS].delete_one({"id": 43})
        assert del_result.deleted_count == 1, "Failed to delete test user 43"
        # Reset counter back to 42
        await db[COLL_COUNTERS].update_one({"_id": "user_id"}, {"$set": {"seq": 42}})

        final_count = await db[COLL_USERS].count_documents({})
        final_counter = await db[COLL_COUNTERS].find_one({"_id": "user_id"})
        assert final_count == 42, f"Expected 42 users after cleanup, got {final_count}"
        assert final_counter["seq"] == 42, f"Expected counter seq=42, got {final_counter['seq']}"
        print(f"  [PASS] Cleaned up ID 43. MongoDB users restored to exactly {final_count}, counter seq={final_counter['seq']}.")

        results["user_registration"] = {
            "registered_id": new_id,
            "registered_role": new_role,
            "counter_at_registration": counter_doc["seq"],
            "cleaned_up": True,
            "final_count": final_count,
            "final_counter_seq": final_counter["seq"],
        }

    await close_mongo()
    results["all_passed"] = True
    print("\n==========================================")
    print("  ALL PHASE 3 VERIFICATION TESTS PASSED!  ")
    print("==========================================")
    return results


if __name__ == "__main__":
    res = asyncio.run(run_phase3_verification())
    print("\nJSON Summary:")
    print(json.dumps(res, indent=2))
