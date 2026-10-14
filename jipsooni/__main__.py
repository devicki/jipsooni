import sys
from jipsooni import digest, kr_proxy, run_once
cmd = sys.argv.pop(1) if len(sys.argv) > 1 else "help"
{"collect": run_once.main, "digest": digest.main, "proxy": kr_proxy.main}.get(cmd, lambda: print("usage: python -m jipsooni collect|digest [...]"))()
