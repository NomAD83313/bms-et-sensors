"""Generate a private MediaMTX config and share one upstream per RTSP camera."""
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
import uuid


class StreamHub:
    def __init__(self, config_path, template_path, rtsp_url):
        self.path = Path(config_path)
        self.template = json.loads(Path(template_path).read_text())
        self.url = rtsp_url.rstrip('/')
        self.previous = None

    def source_url(self, camera):
        if urlsplit(camera['url']).scheme != 'rtsp':
            return camera['url']
        return self.url + '/' + camera['id']

    def sync(self, cameras):
        config = dict(self.template)
        config['paths'] = {
            camera['id']: dict(source=camera['url'], rtspTransport='tcp',
                               sourceOnDemand=True, sourceOnDemandStartTimeout='15s',
                               sourceOnDemandCloseAfter='3s')
            for camera in cameras if urlsplit(camera['url']).scheme == 'rtsp'
        }
        # JSON is valid YAML. Keep credentials out of tracked files and logs.
        content = json.dumps(config, indent=2) + '\n'
        if content == self.previous and self.path.exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name('hub.' + uuid.uuid4().hex + '.tmp')
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, 'w') as stream:
                stream.write(content)
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)
        self.previous = content
