from __future__ import annotations

from pathlib import Path

from ..config import Config
from ..database import Database
from ..downloader import Downloader
from ..sources.radar import RadarResource


def main(config_path: str | None = None) -> None:
    config = Config(config_path)
    database = Database(config.database_path())
    downloader = Downloader(config, database)
    resource = RadarResource()
    resources = resource.discover(config.data)
    for item in resources:
        print(downloader.download_resource(item))
