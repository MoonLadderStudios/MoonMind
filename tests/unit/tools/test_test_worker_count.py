from tools.test_worker_count import worker_count


def test_worker_count_accounts_for_container_memory_and_cpu(tmp_path, monkeypatch):
    monkeypatch.setattr("os.sched_getaffinity", lambda _: set(range(32)), raising=False)
    monkeypatch.setattr(
        "os.sysconf", lambda key: 1024**2 if key == "SC_PHYS_PAGES" else 65536
    )
    (tmp_path / "memory.max").write_text(str(2857 * 1024**2))
    (tmp_path / "cpu.max").write_text("max 100000")
    assert worker_count(cgroup=tmp_path) == 2
    (tmp_path / "memory.max").write_text(str(32 * 1024**3))
    assert worker_count(cgroup=tmp_path) == 31
    (tmp_path / "cpu.max").write_text("150000 100000")
    assert worker_count(cgroup=tmp_path) == 2
    (tmp_path / "memory.max").write_text(str(1024**3))
    assert worker_count(cgroup=tmp_path) == 1
