"""Source families used consistently by filtering, ranking, and allocation."""
ATS_SOURCES = frozenset({
    'greenhouse', 'greenhouse_eu', 'lever', 'ashby', 'workable', 'workday',
    'smartrecruiters', 'rippling', 'bamboohr', 'recruitee', 'personio',
})


def source_family(source: str) -> str:
    return source.split('/')[0]
