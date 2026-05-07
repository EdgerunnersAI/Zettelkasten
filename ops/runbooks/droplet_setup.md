# Droplet Setup Runbook

This runbook documents canonical setup and maintenance procedures for the DigitalOcean droplet running the Zettelkasten bot. Apply these procedures during fresh droplet provisioning and when rebuilding from scratch.

---

## iter-12 Task 24 — vm.swappiness=1

### Why

The 2 GB droplet with Gunicorn `--preload` (Phase 1A, iter-03 optimization) + BGE int8 quantization saw 661 MB swap in use during iter-11 final evaluation (`vm_swap_kb=587` per worker) even when the cgroup memory limit had not been exceeded. The kernel's default `vm.swappiness=10` was preemptively evicting preloaded model pages (in-memory mapped BGE embeddings and Gemini SDK cached models) to make room for file cache, causing thrashing on I/O.

Setting `vm.swappiness=1` changes the kernel's reclaim heuristic: anonymous pages (heap, model state) stay resident under non-critical pressure; only file pages and the page cache participate in eviction. When memory approaches the cgroup limit, swap is still used, preserving correctness. This is independent of (and orthogonal to) the Class P PATH_F mitigation (iter-12 Task 23).

### Run on droplet (bash via SSH)

```bash
sudo tee /etc/sysctl.d/99-zettelkasten.conf > /dev/null <<'SYSCTL'
# iter-12 Task 24 — keep preloaded BGE/Gemini SDK pages resident under
# --preload + 2 GB cgroup. Default 10 was preemptively evicting model
# pages even before genuine memory pressure (iter-11 forensic: 587MB
# /worker swap despite cgroup not at limit).
vm.swappiness=1
SYSCTL
sudo sysctl --system
sysctl vm.swappiness  # expect: vm.swappiness = 1
```

### Verification

After applying the sysctl change, the kernel begins reclaiming existing swap pages as access patterns favor RSS over cache. Over the next ~100 production queries:

- **Before:** `proc_stats.vm_swap_kb` ≈ 587 MB per worker
- **After:** `proc_stats.vm_swap_kb` < 200 MB per worker (target)

Monitor via the droplet's Prometheus endpoint or container `free -h` output in health checks.

### Persistence

The sysctl drop-in at `/etc/sysctl.d/99-zettelkasten.conf` persists across droplet reboots. The `sudo sysctl --system` command loads all drop-ins in `/etc/sysctl.d/` in lexicographic order.

### Rebuild

If the droplet is ever rebuilt from scratch, this section is the canonical procedure for re-applying the swappiness tuning. Run immediately after OS provisioning, before deploying containers.

### References

- https://chrisdown.name/2018/01/02/in-defence-of-swap.html
- https://www.kernel.org/doc/Documentation/sysctl/vm.txt
