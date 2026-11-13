"""Authenticated HTTPS only. No provider credentials, payload or token logging."""

import json
import urllib.error
import urllib.request
from urllib.parse import quote, urlsplit


class GatewayTransportError(Exception):
    def __init__(self, status=None):
        self.status = status
        super().__init__("Gateway HTTPS request was not durably acknowledged.")


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class HttpsBackend:
    def __init__(self, base_url, api_token, robot_token, timeout=15):
        parsed = urlsplit(base_url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or not api_token or not robot_token):
            raise ValueError("Gateway requires an HTTPS base URL and both credentials")
        self._base_url, self._api_token, self._robot_token, self._timeout = base_url.rstrip("/"), api_token, robot_token, timeout
        self._opener = urllib.request.build_opener(NoRedirects())

    def _request(self, method, path, body=None):
        request = urllib.request.Request(self._base_url + path, method=method,
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers={"Authorization": "Bearer " + self._api_token, "X-Robot-Token": self._robot_token,
                     "Content-Type": "application/json"})
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            error.close()
            raise GatewayTransportError(error.code) from None
        except (OSError, ValueError):
            raise GatewayTransportError() from None

    def heartbeat(self, boot_id, active_id, safe_stopped):
        return self._request("POST", "/v1/gateway/heartbeat", {"bootId": boot_id, "activeAssignmentId": active_id, "safeStopped": safe_stopped})

    def poll(self):
        return self._request("GET", "/v1/gateway/assignment")["assignment"]

    def assignment(self, assignment_id):
        return self._request("GET", "/v1/gateway/assignments/" + quote(assignment_id, safe=""))

    def event(self, event):
        return self._request("POST", "/v1/gateway/events", event)
