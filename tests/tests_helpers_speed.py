"""Runs imagegen.make with a fake program and reports which backends it tried."""
from vidgen import imagegen


def attempts(monkeypatch, tmp_path, backend, fails):
    monkeypatch.setattr(imagegen.time, "sleep", lambda _s: None)
    tried = []

    class Run:
        def __init__(self, args):
            self.kind = imagegen.CPU if "--backend" in args else imagegen.GPU
            tried.append(self.kind)
            self.returncode = 1 if self.kind in fails else 0
            if not self.returncode:
                with open(args[args.index("-o") + 1], "wb") as fh:
                    fh.write(b"png")

        def poll(self):
            return self.returncode

    def launch(args, log_path):
        open(log_path, "w").close()
        return Run(args)

    try:
        imagegen.make("a cat", (576, 1024), str(tmp_path / "a.png"), launch=launch,
                      check=lambda: (True, ""), backend=backend, memory=lambda: 16e9,
                      verify=lambda _stop: [])
    except imagegen.ImageError:
        pass
    return tried
