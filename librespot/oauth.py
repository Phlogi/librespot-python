from __future__ import annotations

import base64
import logging
import random
import secrets
import urllib.parse
from hashlib import sha256
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from librespot.proto import Authentication_pb2 as Authentication
import requests


class OAuth:
    logger = logging.getLogger("Librespot:OAuth")
    __spotify_auth = "https://accounts.spotify.com/authorize?response_type=code&client_id=%s&redirect_uri=%s&code_challenge=%s&code_challenge_method=S256&scope=%s&state=%s"
    __scopes = ["app-remote-control", "playlist-modify", "playlist-modify-private", "playlist-modify-public", "playlist-read", "playlist-read-collaborative", "playlist-read-private", "streaming", "ugc-image-upload", "user-follow-modify", "user-follow-read", "user-library-modify", "user-library-read", "user-modify", "user-modify-playback-state", "user-modify-private", "user-personalized", "user-read-birthdate", "user-read-currently-playing", "user-read-email", "user-read-play-history", "user-read-playback-position", "user-read-playback-state", "user-read-private", "user-read-recently-played", "user-top-read"]
    __spotify_token = "https://accounts.spotify.com/api/token"
    __spotify_token_data: dict
    __client_id = ""
    __redirect_url = ""
    __code_verifier = ""
    __code = ""
    __token = ""
    __server = None
    __oauth_url_callback = None
    __success_page_content = None

    def __init__(self, client_id, redirect_url, oauth_url_callback):
        self.__client_id = client_id
        self.__redirect_url = redirect_url
        self.__oauth_url_callback = oauth_url_callback
        self.__spotify_token_data = {"grant_type": "authorization_code", "client_id": "", "redirect_uri": "", "code": "", "code_verifier": ""}
    
    def set_success_page_content(self, content):
        self.__success_page_content = content
        return self

    def __generate_code_verifier(self):
        possible = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
        verifier = ""
        for i in range(128):
            verifier += secrets.choice(possible)
        return verifier

    def __generate_code_challenge(self, code_verifier):
        digest = sha256(code_verifier.encode('utf-8')).digest()
        return base64.urlsafe_b64encode(digest).decode('utf-8').rstrip('=')

    def get_auth_url(self):
        self.__code_verifier = self.__generate_code_verifier()
        self.__oauth_state = secrets.token_urlsafe(32)
        auth_url = self.__spotify_auth % (self.__client_id, self.__redirect_url, self.__generate_code_challenge(self.__code_verifier), "+".join(self.__scopes), self.__oauth_state)
        if self.__oauth_url_callback:
            self.__oauth_url_callback(auth_url)
        return auth_url

    def set_code(self, code):
        self.__code = code

    def request_token(self):
        if not self.__code:
            raise RuntimeError("You need to provide a code before!")
        request_data = self.__spotify_token_data
        request_data["client_id"] = self.__client_id
        request_data["redirect_uri"] = self.__redirect_url
        request_data["code"] = self.__code
        request_data["code_verifier"] = self.__code_verifier
        request = requests.post(
            self.__spotify_token,
            data=request_data,
        )
        if request.status_code != 200:
            raise RuntimeError("Received status code %d: %s" % (request.status_code, request.reason))
        self.__token = request.json()["access_token"]

    def get_credentials(self):
        if not self.__token:
            raise RuntimeError("You need to request a token before!")
        return Authentication.LoginCredentials(
            typ=Authentication.AuthenticationType.AUTHENTICATION_SPOTIFY_TOKEN,
            auth_data=self.__token.encode("utf-8")
        )

    class CallbackServer(HTTPServer):
        callback_path = None

        def __init__(self, server_address, RequestHandlerClass, callback_path, set_code, success_page_content, expected_state):
            self.callback_path = callback_path
            self.set_code = set_code
            self.success_page_content = success_page_content
            self.expected_state = expected_state
            super().__init__(server_address, RequestHandlerClass)

    class CallbackRequestHandler(BaseHTTPRequestHandler):
        server: OAuth.CallbackServer  # type: ignore[assignment]

        def do_GET(self):
            callback_path = self.server.callback_path
            if callback_path is not None and self.path.startswith(callback_path):
                query = urllib.parse.parse_qs(urlparse(self.path).query)
                state_list = query.get("state")
                received_state = state_list[0] if state_list else None
                if received_state != self.server.expected_state:
                    self.send_response(403)
                    self.send_header('Content-type', 'text/html')
                    self.end_headers()
                    self.wfile.write(b"Invalid state parameter (CSRF check failed)")
                    return
                if "code" not in query:
                    self.send_response(400)
                    self.send_header('Content-type', 'text/html')
                    self.end_headers()
                    self.wfile.write(b"Request doesn't contain 'code'")
                    return
                code_list = query.get("code")
                if code_list is None:
                    return
                self.server.set_code(code_list[0])
                self.send_response(200)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                success_page = self.server.success_page_content or "librespot-python received callback"
                self.wfile.write(success_page.encode('utf-8'))
            pass

        # Suppress logging
        def log_message(self, format, *args) -> None:
            return

    def __start_server(self):
        while self.__server is not None and not self.__code:
            try:
                self.__server.handle_request()
            except KeyboardInterrupt:
                return

    def run_callback_server(self):
        url = urlparse(self.__redirect_url)
        self.__server = self.CallbackServer(
            (url.hostname, url.port),
            self.CallbackRequestHandler,
            url.path,
            self.set_code,
            self.__success_page_content,
            self.__oauth_state,
        )
        logging.info("OAuth: Waiting for callback on %s:%s", url.hostname, url.port)
        self.__start_server()
        self._close()

    def flow(self):
        logging.info("OAuth: Visit in your browser and log in: %s ", self.get_auth_url())
        self.run_callback_server()
        self.request_token()
        return self.get_credentials()

    def _close(self):
        if self.__server:
            self.__server.shutdown()
            self.__server = None

