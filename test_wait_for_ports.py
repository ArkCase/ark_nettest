#!/usr/bin/python3
#
# Black-box tests for wait-for-ports: runs the script as a subprocess against
# local test servers and checks the exit code and log output.
#
# usage: test_wait_for_ports.py [path-to-wait-for-ports] [test-name ...]
#
# Needs python3, PyYAML (same as wait-for-ports itself) and the openssl CLI
# (for the HTTPS tests). DNS lookups go through the system resolver, so the DNS
# tests use names it can't resolve (*.invalid) and names it always can (localhost).
#
# Set VERBOSE=1 to also print the output of failing tests.
#
# Set COMPARE_WITH=path/to/original-script to also run the tests marked
# "compare" against that script, and check that both give the same exit code
# and the same probe errors.
#
import atexit
import os
import re
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

def serve(handler, tls = False):
	s, port = listener()
	def handle(c):
		try:
			if tls:
				c = SERVER_TLS.wrap_socket(c, server_side = True)
			handler(c)
		except (OSError, ssl.SSLError):
			c.close()
	def loop():
		while True:
			c, _ = s.accept()
			threading.Thread(target = handle, args = (c,), daemon = True).start()
	threading.Thread(target = loop, daemon = True).start()
	return port

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

class Watcher:
	# Records, for each connection, how long the client kept it open after it
	# got what it needed: the accept (TCP) or the response (HTTP). The server
	# never closes first, so a close is always the client's doing.
	def __init__(self, response = None, tls = False):
		self.response = response
		self.held = []
		self.port = serve(self.handle, tls = tls)

	def handle(self, c):
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

OK = serve(responder("200 OK"))
HTTPS_OK = serve(responder("200 OK"), tls = True)
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

PLAIN_HTTP_ONLY = serve(plain_http_only)
NOT_FOUND = serve(responder("404 Not Found"))
REDIRECT =serve(responder("302 Found", f"Location: http://localhost:{OK}/landing\r\n"))
REDIRECT_TO_404 = serve(responder("302 Found", f"Location: http://localhost:{NOT_FOUND}/landing\r\n"))
RELATIVE_REDIRECT = serve(relative_redirect)
REDIRECT_LOOP = serve(responder("302 Found", "Location: /again\r\n"))
NOT_MODIFIED = serve(responder("304 Not Modified"))
UNAVAILABLE = serve(responder("503 Service Unavailable"))
DROP = serve(drop)
SILENT_PORT = serve(silent)
CLOSED = closed_port()

HTTP_200 = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"
TCP_WATCHER = Watcher()
HTTP_WATCHER = Watcher(HTTP_200)
HTTPS_WATCHER = Watcher(HTTP_200, tls = True)
SILENT_WATCHER = Watcher()

# A dependency that fails slowly (~4s), keeping the script alive so that any
# connection it doesn't close itself stays open (rather than being closed by exit)
KEEP_ALIVE = f"  127.0.0.1:\n    port: {CLOSED}\n    attempts: 3\n    delay: 2\n"

# Each test: config (YAML text), and optionally:
#   via    - "file" (default), "stdin" or "env" (inline config in INIT_DEPENDENCIES)
#   env    - extra environment variables
#   rc     - expected exit code
#   expect - substrings that must appear in the output
#   reject - substrings that must not appear in the output
#   sigint - send SIGINT after this many seconds
#   max_time - the run must finish within this many seconds
#   check  - called after the run, returns a list of problems
#   compare - with COMPARE_WITH set, the other script must produce the same
#            exit code and probe errors (for behavior the rewrite must keep)
TESTS = {
	"http-success": dict(
		config = f"dependencies:\n  localhost:\n    http: http://localhost:{OK}\n",
		compare = True,
		rc = 0,
		expect = [ f"Successfully probed http://localhost:{OK}", "end result: SUCCESS" ],
	),
	"http-dropped-connection-is-retried": dict(
		config = f"dependencies:\n  localhost:\n    http: http://localhost:{DROP}\n    attempts: 2\n    delay: 1\n",
		rc = 1,
		expect = [ "attempt # 1 of 2", "attempt # 2 of 2", "A dependency has failed" ],
		reject = [ "NameError" ],
	),
	"http-timeout-is-honored": dict(
		config = f"dependencies:\n  localhost:\n    http: http://localhost:{SILENT_PORT}\n    attempts: 2\n    delay: 1\n    timeout: 2\n",
		rc = 1,
		expect = [ "timed out (attempt # 2 of 2)" ],
		max_time = 15,
	),
	"http-redirect-is-followed": dict(
		config = f"dependencies:\n  localhost:\n    http: http://localhost:{REDIRECT}\n    attempts: 1\n",
		compare = True,
		rc = 0,
		expect = [ f"Successfully probed http://localhost:{REDIRECT}" ],
	),
	"http-relative-redirect-is-followed": dict(
		config = f"dependencies:\n  localhost:\n    http: http://localhost:{RELATIVE_REDIRECT}/start\n    attempts: 1\n",
		compare = True,
		rc = 0,
		expect = [ f"Successfully probed http://localhost:{RELATIVE_REDIRECT}/start" ],
	),
	"http-redirect-target-failure-fails": dict(
		config = f"dependencies:\n  localhost:\n    http: http://localhost:{REDIRECT_TO_404}\n    attempts: 1\n",
		compare = True,
		rc = 1,
		expect = [ "HTTP Error 404: Not Found" ],
	),
	"http-redirect-loop-fails": dict(
		config = f"dependencies:\n  localhost:\n    http: http://localhost:{REDIRECT_LOOP}\n    attempts: 1\n",
		compare = True,
		rc = 1,
		expect = [ "redirect error that would lead to an infinite loop" ],
	),
	"http-3xx-without-location-fails": dict(
		config = f"dependencies:\n  localhost:\n    http: http://localhost:{NOT_MODIFIED}\n    attempts: 1\n",
		compare = True,
		rc = 1,
		expect = [ "HTTP Error 304: Not Modified" ],
	),
	"http-connection-refused": dict(
		config = f"dependencies:\n  localhost:\n    http: http://localhost:{CLOSED}\n    attempts: 1\n",
		compare = True,
		rc = 1,
		expect = [ "<urlopen error [Errno 111] Connection refused>" ],
	),
	"http-404-is-reported": dict(
		config = f"dependencies:\n  localhost:\n    http: http://localhost:{NOT_FOUND}\n    attempts: 1\n",
		compare = True,
		rc = 1,
		expect = [ "HTTP Error 404: Not Found", "Traceback" ],
	),
	"http-503-is-not-reported": dict(
		config = f"dependencies:\n  localhost:\n    http: http://localhost:{UNAVAILABLE}\n    attempts: 1\n",
		compare = True,
		rc = 1,
		expect = [ "HTTP Error 503: Service Unavailable" ],
		reject = [ "Traceback" ],
	),
	"https-success": dict(
		config = f"dependencies:\n  localhost:\n    http: https://localhost:{HTTPS_OK}\n",
		compare = True,
		env = { "SSL_CERT_FILE" : CERT },
		rc = 0,
		expect = [ f"Successfully probed https://localhost:{HTTPS_OK}" ],
	),
	# Certificates aren't validated: this is only a connectivity probe
	"https-untrusted-certificate-is-accepted": dict(
		config = f"dependencies:\n  localhost:\n    http: https://localhost:{HTTPS_OK}\n    attempts: 1\n",
		rc = 0,
		expect = [ f"Successfully probed https://localhost:{HTTPS_OK}" ],
	),
	"https-hostname-mismatch-is-accepted": dict(
		# The certificate is trusted, but isn't valid for 127.0.0.1
		config = f"dependencies:\n  127.0.0.1:\n    http: https://127.0.0.1:{HTTPS_OK}\n    attempts: 1\n",
		env = { "SSL_CERT_FILE" : CERT },
		rc = 0,
		expect = [ f"Successfully probed https://127.0.0.1:{HTTPS_OK}" ],
	),
	"https-to-non-tls-server-fails": dict(
		config = f"dependencies:\n  localhost:\n    http: https://localhost:{PLAIN_HTTP_ONLY}\n    attempts: 1\n",
		compare = True,
		rc = 1,
		expect = [ "<urlopen error [SSL" ],
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
		expect = [ "Applying the initial delay of 2s" ],
	),
	"empty-ports-is-an-error": dict(
		config = f"mode: any\ndependencies:\n  localhost:\n    ports: []\n  127.0.0.1:\n    port: {CLOSED}\n    attempts: 1\n",
		rc = 1,
		expect = [ "empty list of ports" ],
	),
	"template-mode-is-case-insensitive": dict(
		config = f"template:\n  mode: ANY\ndependencies:\n  localhost:\n    port: {OK}\n",
		rc = 0,
		expect = [ "ports required for success: any" ],
	),
	"missing-config-file-from-env": dict(
		config = None,
		env = { "INIT_DEPENDENCIES" : "/nonexistent/deps.yaml" },
		rc = 1,
		expect = [ "is not a map" ],
		reject = [ "Traceback" ],
	),
	"all-mode-success-with-any-mode-dependency": dict(
		config = f"mode: all\ntemplate:\n  delay: 1\n  attempts: 2\ndependencies:\n  localhost:\n    mode: any\n    ports: [{CLOSED}, {OK}]\n  127.0.0.1:\n    url: http://127.0.0.1:{OK}\n",
		rc = 0,
		expect = [ f"Canceling the task tcp://localhost:{CLOSED}", "All required dependencies have succeeded" ],
	),
	"all-mode-fails-on-first-failure": dict(
		config = f"mode: all\ndependencies:\n  localhost:\n    port: {OK}\n  127.0.0.1:\n    port: {CLOSED}\n    attempts: 1\n",
		rc = 1,
		expect = [ "A dependency has failed, but all were required to succeed" ],
	),
	"any-mode-all-dependencies-fail": dict(
		config = f"mode: any\ntemplate:\n  delay: 1\n  attempts: 2\ndependencies:\n  localhost:\n    ports: [{CLOSED}, {OK}]\n  127.0.0.1:\n    port: {CLOSED}\n",
		rc = 1,
		expect = [ "All required dependencies have failed" ],
	),
	"any-mode-first-success-wins": dict(
		config = f"mode: any\ndependencies:\n  localhost:\n    port: {OK}\n  127.0.0.1:\n    port: {CLOSED}\n    attempts: 50\n",
		rc = 0,
		expect = [ "One dependency has succeeded" ],
		max_time = 10,
	),
	"json-from-stdin": dict(
		config = f'{{"dependencies": {{"localhost": {{"port": {OK}}}}}}}',
		via = "stdin",
		rc = 0,
		expect = [ "Parsing the configuration from stdin", "end result: SUCCESS" ],
	),
	"inline-config-from-env": dict(
		config = f"dependencies:\n  localhost:\n    port: {OK}\n",
		via = "env",
		rc = 0,
		expect = [ "Parsing the configuration from the string", "end result: SUCCESS" ],
	),
	"port-from-envvar": dict(
		config = "dependencies:\n  localhost:\n    port: \"@env:TEST_PORT\"\n",
		env = { "TEST_PORT" : str(OK) },
		rc = 0,
		expect = [ f"Successfully probed tcp://localhost:{OK}" ],
	),
	"no-dependencies": dict(
		config = "mode: all\n",
		rc = 0,
		expect = [ "No dependencies found" ],
	),
	"sigint-exits-promptly": dict(
		config = f"dependencies:\n  localhost:\n    port: {CLOSED}\n    attempts: 50\n",
		sigint = 2,
		rc = 1,
		expect = [ "INTERRUPTED!" ],
		max_time = 5,
	),
	"dns-unknown-host-fails": dict(
		config = f"dependencies:\n  nope.invalid:\n    port: {OK}\n    attempts: 1\n",
		compare = True,
		rc = 1,
		expect = [ "Name or service not known" ],
	),
	"dns-unknown-host-fails-for-http": dict(
		config = f"dependencies:\n  nope.invalid:\n    http: http://nope.invalid:{OK}/\n    attempts: 1\n",
		compare = True,
		rc = 1,
		expect = [ "<urlopen error [Errno -2] Name or service not known>" ],
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

RE_PROBE_ERROR = re.compile(r" == (.*?) \(attempt # \d+ of \d+\)", re.DOTALL)

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
	for s in t.get("expect", []):
		if s not in out:
			problems.append(f"missing output: {s!r}")
	for s in t.get("reject", []):
		if s in out:
			problems.append(f"unexpected output: {s!r}")
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
			errors, ref_errors = RE_PROBE_ERROR.findall(out), RE_PROBE_ERROR.findall(ref_out)
			if errors != ref_errors:
				problems.append(f"probe errors {errors}, the reference script gave {ref_errors}")
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
