import os
import shutil
import subprocess
import logging
from rest_framework import permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response

logger = logging.getLogger('api')


class SystemStatusView(APIView):
    """
    GET /api/system/status
    Retrieves system resource utilization (load avg, RAM, and Disk space).
    """
    permission_classes = (permissions.AllowAny,)

    def get(self, request):
        status_data = {}

        # 1. Disk usage
        try:
            total, used, free = shutil.disk_usage("/")
            status_data['disk'] = {
                'total_gb': round(total / (2**30), 2),
                'used_gb': round(used / (2**30), 2),
                'free_gb': round(free / (2**30), 2),
                'used_percent': round((used / total) * 100, 2)
            }
        except Exception as e:
            status_data['disk'] = {"error": str(e)}

        # 2. CPU load average (Linux specific)
        try:
            load1, load5, load15 = os.getloadavg()
            status_data['cpu'] = {
                'load_1m': load1,
                'load_5m': load5,
                'load_15m': load15
            }
        except Exception:
            status_data['cpu'] = {"info": "Only supported on Unix environments"}

        # 3. RAM usage (Linux specific)
        try:
            meminfo = {}
            with open('/proc/meminfo', 'r') as f:
                for line in f:
                    parts = line.split(':')
                    if len(parts) == 2:
                        name = parts[0].strip()
                        value = parts[1].split()[0].strip()
                        meminfo[name] = int(value)
            
            total_kb = meminfo.get('MemTotal', 1)
            free_kb = meminfo.get('MemFree', 0)
            buffers_kb = meminfo.get('Buffers', 0)
            cached_kb = meminfo.get('Cached', 0)
            
            # Real free memory
            available_kb = free_kb + buffers_kb + cached_kb
            used_kb = total_kb - available_kb

            status_data['memory'] = {
                'total_mb': round(total_kb / 1024, 2),
                'used_mb': round(used_kb / 1024, 2),
                'free_mb': round(available_kb / 1024, 2),
                'used_percent': round((used_kb / total_kb) * 100, 2)
            }
        except Exception:
            status_data['memory'] = {"info": "Only supported on Unix environments"}

        return Response(status_data, status=status.HTTP_200_OK)


class SystemVersionView(APIView):
    """
    GET /api/system/version
    Returns ZFetch Backend version details.
    """
    permission_classes = (permissions.AllowAny,)

    def get(self, request):
        return Response({
            "name": "ZFetch Backend",
            "version": "1.0.0",
            "environment": "production" if not os.getenv('DEBUG', 'True').lower() == 'true' else "development"
        }, status=status.HTTP_200_OK)


class SystemDependenciesView(APIView):
    """
    GET /api/system/dependencies
    Checks availability and version of critical engine systems (yt-dlp, ffmpeg, aria2c).
    """
    permission_classes = (permissions.AllowAny,)

    def _get_version(self, command: str, version_flag: str = '--version') -> str:
        try:
            process = subprocess.Popen(
                [command, version_flag], 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE, 
                text=True
            )
            stdout, stderr = process.communicate(timeout=3)
            if process.returncode == 0:
                # Get first line of version info
                return stdout.split('\n')[0].strip()
            else:
                return f"Error: {stderr.strip()}"
        except Exception:
            return "Not Installed"

    def get(self, request):
        dependencies = {
            "yt-dlp": self._get_version("yt-dlp"),
            "ffmpeg": self._get_version("ffmpeg", "-version"),
            "aria2c": self._get_version("aria2c", "--version"),
        }
        return Response(dependencies, status=status.HTTP_200_OK)
