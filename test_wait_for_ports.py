#!/usr/bin/python3
#
# Black-box tests for wait-for-ports: runs the script as a subprocess against
# local test servers, and checks its behavior through its exit code, timing,
# and the connections the test servers receive. Log text is only checked for
# tracebacks, since whether a failure gets reported with one is part of the
# script's intended behavior.
#
# usage: test_wait_for_ports.py [path-to-wait-for-ports] [test-name ...]
#
# Needs python3, PyYAML (same as wait-for-ports itself) and the openssl CLI
# (for the HTTPS tests). DNS lookups go through the system resolver, so the DNS
# tests use a name it can't resolve (nope.invalid).
#
# Set VERBOSE=1 to also print the output of failing tests.
#
# Set COMPARE_WITH=path/to/original-script to also run the tests marked
# "compare" against that script, and check that both give the same exit code,
# and either both or neither report a traceback.
#
import atexit
import os
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TMPDIR = tempfile.mkdtemp(prefix = "wait-for-ports-test-")
atexit.register(shutil.rmtree, TMPDIR, True)

def write_file(name, content):
	path = os.path.join(TMPDIR, name)
	with open(path, "w") as f:
		f.write(content)
	return path

#
# TLS
#
CERT = os.path.join(TMPDIR, "cert.pem")
KEY = os.path.join(TMPDIR, "key.pem")
subprocess.run([ "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
	"-keyout", KEY, "-out", CERT, "-subj", "/CN=localhost",
	"-addext", "subjectAltName=DNS:localhost" ],
	check = True, capture_output = True)
SERVER_TLS = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
SERVER_TLS.load_cert_chain(CERT, KEY)

#
# TCP servers
#
def listener(host = "127.0.0.1", port = 0):
	s = socket.socket()
	s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
	s.bind((host, port))
	s.listen(16)
	return s, s.getsockname()[1]

class Server:
	# Runs handler(connection) for each connection, and counts them
	def __init__(self, handler, tls = False):
		self.handler = handler
		self.tls = tls
		self.connections = 0
		self.lock = threading.Lock()
		self.socket, self.port = listener()
		threading.Thread(target = self.loop, daemon = True).start()

	def loop(self):
		while True:
			c, _ = self.socket.accept()
			with self.lock:
				self.connections += 1
			threading.Thread(target = self.handle, args = (c,), daemon = True).start()

	def handle(self, c):
		try:
			if self.tls:
				c = SERVER_TLS.wrap_socket(c, server_side = True)
			self.handler(c)
		except (OSError, ssl.SSLError):
			c.close()

	def expect_connections(self, expected):
		# Give the accept loop a moment to catch up, then check (and reset) the count
		deadline = time.monotonic() + 2
		while (self.connections < expected) and (time.monotonic() < deadline):
			time.sleep(0.05)
		time.sleep(0.1)
		with self.lock:
			seen, self.connections = self.connections, 0
		if seen != expected:
			return [ f"the server on port {self.port} got {seen} connections, expected {expected}" ]
		return []

def read_head(c):
	data = b""
	while b"\r\n\r\n" not in data:
		chunk = c.recv(4096)
		if not chunk:
			break
		data += chunk
	return data.decode("latin-1")

def responder(status, headers = ""):
	def handler(c):
		read_head(c)
		c.sendall(f"HTTP/1.1 {status}\r\n{headers}Content-Length: 2\r\nConnection: close\r\n\r\nok".encode())
		c.close()
	return handler

def drop(c):
	# Accept, then hang up without answering
	c.close()

SILENT = []
def silent(c):
	# Accept and never answer (keep the socket alive)
	SILENT.append(c)

def relative_redirect(c):
	# Redirects everything to /landing, which answers 200
	head = read_head(c)
	if head.split(" ", 2)[1] == "/landing":
		c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
	else:
		c.sendall(b"HTTP/1.1 301 Moved Permanently\r\nLocation: /landing\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
	c.close()

def plain_http_only(c):
	# Answers whatever arrives (i.e. a TLS ClientHello) with a plain HTTP error
	c.recv(4096)
	c.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
	c.close()

class Watcher(Server):
	# Records, for each connection, how long the client kept it open after it
	# got what it needed: the accept (TCP) or the response (HTTP). The server
	# never closes first, so a close is always the client's doing.
	def __init__(self, response = None, tls = False):
		self.response = response
		self.held = []
		super().__init__(self.watch, tls = tls)

	def watch(self, c):
		if self.response:
			read_head(c)
			c.sendall(self.response)
		start = time.monotonic()
		c.settimeout(30)
		try:
			while c.recv(4096):
				pass
			if isinstance(c, ssl.SSLSocket):
				# That was just the TLS close_notify, which we don't answer:
				# now wait for the TCP connection itself to be closed
				with socket.socket(fileno = os.dup(c.fileno())) as raw:
					raw.settimeout(30)
					while raw.recv(4096):
						pass
		except (OSError, ssl.SSLError):
			pass
		self.held.append(time.monotonic() - start)
		c.close()

	def check(self, limit, count = 1):
		# Wait for the closes to be recorded
		deadline = time.monotonic() + 35
		while (len(self.held) < count) and (time.monotonic() < deadline):
			time.sleep(0.1)
		held = self.held[:]
		self.held.clear()
		if len(held) < count:
			return [ f"saw {len(held)} closed connections, expected {count}" ]
		return [ f"a connection was held open {h:.1f}s after it was needed (limit {limit}s)" for h in held[:count] if h > limit ]

def closed_port():
	s, port = listener()
	s.close()
	return port

OK = Server(responder("200 OK")).port
HTTPS_OK = Server(responder("200 OK"), tls = True).port
PLAIN_HTTP_ONLY = Server(plain_http_only).port
NOT_FOUND = Server(responder("404 Not Found")).port
REDIRECT = Server(responder("302 Found", f"Location: http://localhost:{OK}/landing\r\n")).port
REDIRECT_TO_404 = Server(responder("302 Found", f"Location: http://localhost:{NOT_FOUND}/landing\r\n")).port
RELATIVE_REDIRECT = Server(relative_redirect).port
REDIRECT_LOOP = Server(responder("302 Found", "Location: /again\r\n")).port
NOT_MODIFIED = Server(responder("304 Not Modified")).port
UNAVAILABLE = Server(responder("503 Service Unavailable")).port
SLOW = Server(silent).port
CLOSED = closed_port()

# Servers whose connection counts the tests check
DROPPER = Server(drop)
SILENT_SERVER = Server(silent)
STDIN_TARGET = Server(responder("200 OK"))
ENV_TARGET = Server(responder("200 OK"))
ENVVAR_PORT_TARGET = Server(responder("200 OK"))
NEVER_PROBED = Server(responder("200 OK"))

HTTP_200 = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"
TCP_WATCHER = Watcher()
HTTP_WATCHER = Watcher(HTTP_200)
HTTPS_WATCHER = Watcher(HTTP_200, tls = True)
SILENT_WATCHER = Watcher()

# A dependency that fails slowly (~4s), keeping the script alive so that any
# connection it doesn't close itself stays open (rather than being closed by exit)
KEEP_ALIVE = f"  127.0.0.1:\n    port: {CLOSED}\n    attempts: 3\n    delay: 2\n"

# A name the system resolver must fail to resolve
UNKNOWN_HOST = "nope.invalid"

def unknown_host_check():
	try:
		socket.gethostbyname(UNKNOWN_HOST)
		return [ f"this system's resolver resolves {UNKNOWN_HOST}, so this test can't run here" ]
	except socket.gaierror:
		return []

# Each test: config (YAML text), and optionally:
#   via       - "file" (default), "stdin" or "env" (inline config in INIT_DEPENDENCIES)
#   env       - extra environment variables
#   rc        - expected exit code
#   traceback - True if a traceback must be reported, False if it must not
#   sigint    - send SIGINT after this many seconds
#   min_time  - the run must take at least this many seconds
#   max_time  - the run must finish within this many seconds
#   check     - called after the run, returns a list of problems
#   compare   - with COMPARE_WITH set, the other script must give the same exit
#               code and the same traceback reporting (for behavior the rewrite must keep)
TESTS = {
	"http-success": dict(
		config = f"dependencies:\n  localhost:\n    http: http://localhost:{OK}\n",
		compare = True,
		rc = 0,
	),
	"http-dropped-connection-is-retried": dict(
		config = f"dependencies:\n  localhost:\n    http: http://localhost:{DROPPER.port}\n    attempts: 2\n    delay: 1\n",
		rc = 1,
		traceback = False,
		check = lambda: DROPPER.expect_connections(2),
	),
	"http-timeout-is-honored": dict(
		config = f"dependencies:\n  localhost:\n    http: http://localhost:{SILENT_SERVER.port}\n    attempts: 2\n    delay: 1\n    timeout: 2\n",
		rc = 1,
		max_time = 15,
		check = lambda: SILENT_SERVER.expect_connections(2),
	),
	"http-redirect-is-followed": dict(
		config = f"dependencies:\n  localhost:\n    http: http://localhost:{REDIRECT}\n    attempts: 1\n",
		compare = True,
		rc = 0,
	),
	"http-relative-redirect-is-followed": dict(
		config = f"dependencies:\n  localhost:\n    http: http://localhost:{RELATIVE_REDIRECT}/start\n    attempts: 1\n",
		compare = True,
		rc = 0,
	),
	"http-redirect-target-failure-fails": dict(
		config = f"dependencies:\n  localhost:\n    http: http://localhost:{REDIRECT_TO_404}\n    attempts: 1\n",
		compare = True,
		rc = 1,
	),
	"http-redirect-loop-fails": dict(
		config = f"dependencies:\n  localhost:\n    http: http://localhost:{REDIRECT_LOOP}\n    attempts: 1\n",
		compare = True,
		rc = 1,
		max_time = 10,
	),
	"http-3xx-without-location-fails": dict(
		config = f"dependencies:\n  localhost:\n    http: http://localhost:{NOT_MODIFIED}\n    attempts: 1\n",
		compare = True,
		rc = 1,
	),
	"http-connection-refused-is-not-reported": dict(
		config = f"dependencies:\n  localhost:\n    http: http://localhost:{CLOSED}\n    attempts: 1\n",
		compare = True,
		rc = 1,
		traceback = False,
	),
	"http-404-is-reported": dict(
		config = f"dependencies:\n  localhost:\n    http: http://localhost:{NOT_FOUND}\n    attempts: 1\n",
		compare = True,
		rc = 1,
		traceback = True,
	),
	"http-503-is-not-reported": dict(
		config = f"dependencies:\n  localhost:\n    http: http://localhost:{UNAVAILABLE}\n    attempts: 1\n",
		compare = True,
		rc = 1,
		traceback = False,
	),
	"https-success": dict(
		config = f"dependencies:\n  localhost:\n    http: https://localhost:{HTTPS_OK}\n",
		compare = True,
		env = { "SSL_CERT_FILE" : CERT },
		rc = 0,
	),
	# Certificates aren't validated: this is only a connectivity probe
	"https-untrusted-certificate-is-accepted": dict(
		config = f"dependencies:\n  localhost:\n    http: https://localhost:{HTTPS_OK}\n    attempts: 1\n",
		rc = 0,
	),
	"https-hostname-mismatch-is-accepted": dict(
		# The certificate is trusted, but isn't valid for 127.0.0.1
		config = f"dependencies:\n  127.0.0.1:\n    http: https://127.0.0.1:{HTTPS_OK}\n    attempts: 1\n",
		env = { "SSL_CERT_FILE" : CERT },
		rc = 0,
	),
	"https-to-non-tls-server-fails": dict(
		config = f"dependencies:\n  localhost:\n    http: https://localhost:{PLAIN_HTTP_ONLY}\n    attempts: 1\n",
		compare = True,
		rc = 1,
	),
	"tcp-probe-closes-its-connection": dict(
		config = f"dependencies:\n  localhost:\n    port: {TCP_WATCHER.port}\n{KEEP_ALIVE}",
		rc = 1,
		check = lambda: TCP_WATCHER.check(1),
	),
	"http-probe-closes-its-connection": dict(
		config = f"dependencies:\n  localhost:\n    http: http://localhost:{HTTP_WATCHER.port}\n{KEEP_ALIVE}",
		rc = 1,
		check = lambda: HTTP_WATCHER.check(1),
	),
	"https-probe-closes-its-connection": dict(
		config = f"dependencies:\n  localhost:\n    http: https://localhost:{HTTPS_WATCHER.port}\n{KEEP_ALIVE}",
		env = { "SSL_CERT_FILE" : CERT },
		rc = 1,
		check = lambda: HTTPS_WATCHER.check(1),
	),
	"timed-out-probe-closes-its-connection": dict(
		# Held open for the 1s timeout, then it must be closed before the retry
		config = f"dependencies:\n  localhost:\n    http: http://localhost:{SILENT_WATCHER.port}\n    attempts: 2\n    delay: 3\n    timeout: 1\n",
		rc = 1,
		check = lambda: SILENT_WATCHER.check(2, count = 2),
	),
	"initial-delay-before-first-attempt": dict(
		config = f"dependencies:\n  localhost:\n    port: {OK}\n    attempts: 1\n    initialDelay: 2\n",
		rc = 0,
		min_time = 2,
	),
	"empty-ports-is-an-error": dict(
		# Only one dependency needs to succeed, and the other one would: so only
		# rejecting the configuration (before probing anything) can fail this
		config = f"mode: any\ndependencies:\n  localhost:\n    ports: []\n  127.0.0.1:\n    port: {NEVER_PROBED.port}\n",
		rc = 1,
		traceback = False,
		check = lambda: NEVER_PROBED.expect_connections(0),
	),
	"template-mode-is-case-insensitive": dict(
		# With "any" from the template, the open port is enough
		config = f"template:\n  mode: ANY\n  attempts: 1\ndependencies:\n  localhost:\n    ports: [{CLOSED}, {OK}]\n",
		rc = 0,
	),
	"missing-config-file-from-env": dict(
		config = None,
		env = { "INIT_DEPENDENCIES" : "/nonexistent/deps.yaml" },
		rc = 1,
		traceback = False,
	),
	"any-mode-dependency-succeeds-on-first-success": dict(
		# Waiting for the closed port's retries would take at least 3s
		config = f"mode: all\ntemplate:\n  delay: 3\n  attempts: 2\ndependencies:\n  localhost:\n    mode: any\n    ports: [{CLOSED}, {OK}]\n  127.0.0.1:\n    url: http://127.0.0.1:{OK}\n",
		rc = 0,
		max_time = 2,
	),
	"all-mode-fails-on-first-failure": dict(
		# Waiting for the slow dependency would take 10s
		config = f"mode: all\ndependencies:\n  localhost:\n    port: {OK}\n  127.0.0.1:\n    port: {CLOSED}\n    attempts: 1\n  slow:\n    http: http://localhost:{SLOW}\n    attempts: 1\n    timeout: 10\n",
		rc = 1,
		max_time = 5,
	),
	"any-mode-all-dependencies-fail": dict(
		config = f"mode: any\ntemplate:\n  delay: 1\n  attempts: 2\ndependencies:\n  localhost:\n    ports: [{CLOSED}, {OK}]\n  127.0.0.1:\n    port: {CLOSED}\n",
		rc = 1,
	),
	"any-mode-first-success-wins": dict(
		config = f"mode: any\ndependencies:\n  localhost:\n    port: {OK}\n  127.0.0.1:\n    port: {CLOSED}\n    attempts: 50\n",
		rc = 0,
		max_time = 10,
	),
	"any-mode-first-http-success-wins": dict(
		config = f"mode: any\ndependencies:\n  localhost:\n    http: http://localhost:{OK}\n  127.0.0.1:\n    port: {CLOSED}\n    attempts: 50\n",
		rc = 0,
		max_time = 10,
	),
	"json-from-stdin": dict(
		config = f'{{"dependencies": {{"localhost": {{"port": {STDIN_TARGET.port}}}}}}}',
		via = "stdin",
		rc = 0,
		check = lambda: STDIN_TARGET.expect_connections(1),
	),
	"inline-config-from-env": dict(
		config = f"dependencies:\n  localhost:\n    port: {ENV_TARGET.port}\n",
		via = "env",
		rc = 0,
		check = lambda: ENV_TARGET.expect_connections(1),
	),
	"port-from-envvar": dict(
		config = "dependencies:\n  localhost:\n    port: \"@env:TEST_PORT\"\n",
		env = { "TEST_PORT" : str(ENVVAR_PORT_TARGET.port) },
		rc = 0,
		check = lambda: ENVVAR_PORT_TARGET.expect_connections(1),
	),
	"no-dependencies": dict(
		config = "mode: all\n",
		rc = 0,
	),
	"sigint-exits-promptly": dict(
		config = f"dependencies:\n  localhost:\n    port: {CLOSED}\n    attempts: 50\n",
		sigint = 2,
		rc = 1,
		max_time = 5,
	),
	"dns-unknown-host-fails": dict(
		config = f"dependencies:\n  {UNKNOWN_HOST}:\n    port: {OK}\n    attempts: 1\n",
		compare = True,
		rc = 1,
		check = unknown_host_check,
	),
	"dns-unknown-host-fails-for-http": dict(
		config = f"dependencies:\n  {UNKNOWN_HOST}:\n    http: http://{UNKNOWN_HOST}:{OK}/\n    attempts: 1\n",
		compare = True,
		rc = 1,
		check = unknown_host_check,
	),
}

# Variables that would change how the script (or the original) connects, which must come only from the tests
SCRUBBED_ENV = [ "INIT_DEPENDENCIES", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUEST_METHOD" ]

def clean_env():
	env = dict(os.environ)
	for k in list(env):
		if (k in SCRUBBED_ENV) or k.lower().endswith("_proxy"):
			del env[k]
	return env

def has_traceback(out):
	return "Traceback" in out

def run_script(script, name, t):
	# Returns (exit code, output, elapsed), or (None, output, None) if it hung
	env = clean_env()
	env.update(t.get("env", {}))
	args = [ sys.executable, script ]
	via = t.get("via", "file")
	if t["config"] is not None:
		if via == "file":
			args.append(write_file(f"{name}.yaml", t["config"]))
		elif via == "stdin":
			args.append("-")
		elif via == "env":
			env["INIT_DEPENDENCIES"] = t["config"]

	start = time.monotonic()
	p = subprocess.Popen(args, env = env, stdin = (subprocess.PIPE if via == "stdin" else subprocess.DEVNULL), stdout = subprocess.PIPE, stderr = subprocess.STDOUT, text = True)
	if "sigint" in t:
		threading.Timer(t["sigint"], p.send_signal, args = (signal.SIGINT,)).start()
	try:
		out = p.communicate(input = (t["config"] if via == "stdin" else None), timeout = 60)[0]
	except subprocess.TimeoutExpired:
		p.kill()
		return None, p.communicate()[0], None
	return p.returncode, out, time.monotonic() - start

def run_test(script, name, t):
	rc, out, elapsed = run_script(script, name, t)
	if rc is None:
		return [ "timed out after 60s (hung?)" ], out

	problems = []
	if rc != t["rc"]:
		problems.append(f"exit code {rc}, expected {t['rc']}")
	if ("traceback" in t) and (has_traceback(out) != t["traceback"]):
		problems.append("a traceback was reported" if t["traceback"] is False else "no traceback was reported")
	if ("min_time" in t) and (elapsed < t["min_time"]):
		problems.append(f"took {elapsed:.1f}s, expected at least {t['min_time']}s")
	if ("max_time" in t) and (elapsed > t["max_time"]):
		problems.append(f"took {elapsed:.1f}s, limit {t['max_time']}s")
	if "check" in t:
		problems += t["check"]()

	reference = os.environ.get("COMPARE_WITH")
	if reference and t.get("compare"):
		ref_rc, ref_out, _ = run_script(os.path.abspath(reference), name, t)
		if ref_rc is None:
			problems.append("the reference script timed out after 60s (hung?)")
		else:
			if rc != ref_rc:
				problems.append(f"exit code {rc}, the reference script gave {ref_rc}")
			if has_traceback(out) != has_traceback(ref_out):
				problems.append(f"traceback reported: {has_traceback(out)}, by the reference script: {has_traceback(ref_out)}")
	return problems, out

def main():
	script = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "wait-for-ports"))
	selected = sys.argv[2:] or list(TESTS)
	failed = 0
	for name in selected:
		problems, out = run_test(script, name, TESTS[name])
		if problems:
			failed += 1
			print(f"FAIL  {name}")
			for p in problems:
				print(f"      - {p}")
			if os.environ.get("VERBOSE"):
				print("      " + out.replace("\n", "\n      "))
		else:
			print(f"ok    {name}")
	print(f"\n{len(selected) - failed}/{len(selected)} passed")
	sys.exit(1 if failed else 0)

if __name__ == "__main__":
	main()
