from __future__ import annotations

from ...projects.models import ProjectBinding


def backend_for(binding: ProjectBinding):
    if binding.environment == "windows":
        from .windows import WindowsBackend

        return WindowsBackend(binding)
    from .wsl import WslBackend

    return WslBackend(binding)
