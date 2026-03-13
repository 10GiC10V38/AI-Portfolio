"""
shared/notifications/push.py

Push notification dispatcher.
Supports: FCM (Android), APNs (iOS), Web Push (browser).
Called by agents after writing an alert to DB.
"""
import os
import json
import logging
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class PushPayload:
    title: str
    body: str
    severity: str           # critical | warning | info | opportunity
    alert_id: str
    ticker: Optional[str] = None
    data: Optional[dict] = None


def dispatch_alert(user_id: str, payload: PushPayload) -> None:
    """
    Called after an alert is written to DB.
    Fetches the user's push subscriptions and dispatches to all active ones.
    """
    from db.connection import get_conn
    from psycopg2.extras import RealDictCursor

    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT platform, token FROM push_subscriptions
                WHERE user_id = %s AND is_active = TRUE
                """,
                (user_id,)
            )
            subscriptions = [dict(r) for r in cur.fetchall()]

    if not subscriptions:
        logger.debug(f"No push subscriptions for user {user_id}")
        return

    for sub in subscriptions:
        try:
            if sub["platform"] == "fcm":
                _send_fcm(sub["token"], payload)
            elif sub["platform"] == "apns":
                _send_apns(sub["token"], payload)
            elif sub["platform"] == "web":
                _send_web_push(sub["token"], payload)
        except Exception as e:
            logger.error(f"Push failed for {sub['platform']}: {e}")


# ── FCM (Android + Web via Firebase) ─────────────────────────────────────────

def _send_fcm(device_token: str, payload: PushPayload) -> None:
    """
    Firebase Cloud Messaging — free, no limits for standard notifications.
    Requires FIREBASE_PROJECT_ID and a service account key in GCP Secret Manager.
    """
    import httpx

    try:
        import google.auth
        import google.auth.transport.requests
        from google.oauth2 import service_account
    except ImportError:
        logger.warning("google-auth not installed — FCM disabled")
        return

    project_id = os.getenv("FIREBASE_PROJECT_ID", "")
    if not project_id:
        logger.warning("FIREBASE_PROJECT_ID not set — FCM skipped")
        return

    # Severity → Android notification priority
    android_priority = "high" if payload.severity in ("critical", "warning") else "normal"
    # Severity → notification color
    colors = {
        "critical":    "#E24B4A",
        "warning":     "#EF9F27",
        "info":        "#378ADD",
        "opportunity": "#1D9E75",
    }

    message = {
        "message": {
            "token": device_token,
            "notification": {
                "title": payload.title,
                "body":  payload.body,
            },
            "android": {
                "priority": android_priority,
                "notification": {
                    "color":        colors.get(payload.severity, "#378ADD"),
                    "channel_id":   f"portfolio_ai_{payload.severity}",
                    "click_action": "OPEN_ALERT",
                },
            },
            "data": {
                "alert_id": payload.alert_id,
                "severity": payload.severity,
                "ticker":   payload.ticker or "",
            },
        }
    }

    # Get OAuth2 token for FCM v1 API
    creds = _get_firebase_credentials()
    if not creds:
        return

    auth_req = google.auth.transport.requests.Request()
    creds.refresh(auth_req)

    resp = httpx.post(
        f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send",
        headers={
            "Authorization": f"Bearer {creds.token}",
            "Content-Type":  "application/json",
        },
        content=json.dumps(message),
        timeout=10.0,
    )
    resp.raise_for_status()
    logger.info(f"FCM sent | alert_id={payload.alert_id} token=...{device_token[-6:]}")


def _get_firebase_credentials():
    """Load Firebase service account from GCP Secret Manager or env."""
    secrets_source = os.getenv("SECRETS_SOURCE", "env")
    try:
        from google.oauth2 import service_account

        if secrets_source == "gcp":
            from google.cloud import secretmanager
            client = secretmanager.SecretManagerServiceClient()
            project = os.environ["GCP_PROJECT_ID"]
            path = f"projects/{project}/secrets/firebase-service-account/versions/latest"
            response = client.access_secret_version(request={"name": path})
            sa_info = json.loads(response.payload.data.decode("UTF-8"))
        else:
            sa_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "")
            if not sa_json:
                logger.warning("FIREBASE_SERVICE_ACCOUNT_JSON not set")
                return None
            sa_info = json.loads(sa_json)

        return service_account.Credentials.from_service_account_info(
            sa_info,
            scopes=["https://www.googleapis.com/auth/firebase.messaging"],
        )
    except Exception as e:
        logger.error(f"Firebase credentials error: {e}")
        return None


# ── APNs (iOS) ────────────────────────────────────────────────────────────────

def _send_apns(device_token: str, payload: PushPayload) -> None:
    """
    Apple Push Notification service — free.
    Requires APNs auth key (.p8 file) from Apple Developer account.
    Stored in GCP Secret Manager as 'apns-auth-key'.
    """
    try:
        import jwt as pyjwt    # PyJWT
        import httpx
    except ImportError:
        logger.warning("PyJWT not installed — APNs disabled")
        return

    team_id  = os.getenv("APNS_TEAM_ID", "")
    key_id   = os.getenv("APNS_KEY_ID", "")
    bundle_id = os.getenv("APNS_BUNDLE_ID", "com.yourname.portfolioai")

    if not all([team_id, key_id]):
        logger.debug("APNs not configured — skipping")
        return

    # Load .p8 key
    auth_key = _load_apns_key()
    if not auth_key:
        return

    # Generate JWT for APNs auth
    token = pyjwt.encode(
        {"iss": team_id, "iat": __import__("time").time()},
        auth_key,
        algorithm="ES256",
        headers={"kid": key_id},
    )

    # Map severity to APNs interruption level
    interruption_level = "time-sensitive" if payload.severity == "critical" else "active"

    notification = {
        "aps": {
            "alert": {"title": payload.title, "body": payload.body},
            "badge": 1,
            "sound": "default",
            "interruption-level": interruption_level,
        },
        "alert_id": payload.alert_id,
        "severity": payload.severity,
    }

    # APNs HTTP/2 endpoint
    apns_host = "api.push.apple.com"     # production
    url = f"https://{apns_host}/3/device/{device_token}"

    resp = httpx.post(
        url,
        headers={
            "authorization":  f"bearer {token}",
            "apns-topic":     bundle_id,
            "apns-push-type": "alert",
            "apns-priority":  "10" if payload.severity == "critical" else "5",
        },
        content=json.dumps(notification),
        timeout=10.0,
    )

    if resp.status_code != 200:
        logger.error(f"APNs error {resp.status_code}: {resp.text}")
    else:
        logger.info(f"APNs sent | alert_id={payload.alert_id}")


def _load_apns_key() -> Optional[str]:
    secrets_source = os.getenv("SECRETS_SOURCE", "env")
    if secrets_source == "gcp":
        try:
            from google.cloud import secretmanager
            client = secretmanager.SecretManagerServiceClient()
            project = os.environ["GCP_PROJECT_ID"]
            path = f"projects/{project}/secrets/apns-auth-key/versions/latest"
            response = client.access_secret_version(request={"name": path})
            return response.payload.data.decode("UTF-8")
        except Exception as e:
            logger.error(f"Failed to load APNs key from GCP: {e}")
            return None
    return os.getenv("APNS_AUTH_KEY", "")


# ── Web Push ──────────────────────────────────────────────────────────────────

def _send_web_push(subscription_json: str, payload: PushPayload) -> None:
    """
    Web Push API — browser notifications.
    Uses pywebpush library. VAPID keys stored in GCP Secret Manager.
    """
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        logger.warning("pywebpush not installed — Web Push disabled")
        return

    vapid_private_key = os.getenv("VAPID_PRIVATE_KEY", "")
    vapid_email       = os.getenv("VAPID_EMAIL", "admin@portfolioai.app")

    if not vapid_private_key:
        logger.debug("VAPID keys not configured — skipping Web Push")
        return

    try:
        subscription_info = json.loads(subscription_json)
        data = json.dumps({
            "title":    payload.title,
            "body":     payload.body,
            "severity": payload.severity,
            "alert_id": payload.alert_id,
            "ticker":   payload.ticker,
        })

        webpush(
            subscription_info=subscription_info,
            data=data,
            vapid_private_key=vapid_private_key,
            vapid_claims={"sub": f"mailto:{vapid_email}"},
        )
        logger.info(f"Web Push sent | alert_id={payload.alert_id}")

    except WebPushException as e:
        if "410" in str(e):
            logger.info("Web Push subscription expired — should deactivate in DB")
        else:
            logger.error(f"Web Push failed: {e}")
