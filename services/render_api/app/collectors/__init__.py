from app.collectors.bluesky import Bluesky
from app.collectors.commoncrawl import CommonCrawl
from app.collectors.youtube import YouTube

ADAPTERS = {"bluesky": Bluesky, "youtube": YouTube, "commoncrawl": CommonCrawl}
