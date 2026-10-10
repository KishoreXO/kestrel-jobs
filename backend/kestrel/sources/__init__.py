from kestrel.sources.adzuna import AdzunaSource
from kestrel.sources.ats import AshbySource, GreenhouseSource, LeverSource
from kestrel.sources.feeds import FeedSource
from kestrel.sources.usajobs import USAJobsSource

ALL_SOURCES = [GreenhouseSource(), LeverSource(), AshbySource(), USAJobsSource(), AdzunaSource(), FeedSource()]

__all__ = ["ALL_SOURCES"]
