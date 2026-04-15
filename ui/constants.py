"""
Shared constants: node labels, pipeline ordering, status colours, CSS.
"""

NODE_LABELS: dict[str, str] = {
    "resumption_inspector": "Check session state",
    "intent_parser":        "Parse request",
    "web_discovery":        "Search GitHub repositories",
    "confirm_package":      "Confirm package",
    "docs_discovery":       "Find documentation URL",
    "context7_agent":       "Fetch Context7 docs",
    "docs_scraper":         "Scrape official docs",
    "github_agent":         "Ingest GitHub source",
    "aggregator":           "Aggregate content",
    "quality_judge":        "Evaluate documentation quality",
    "enrichment_agent":     "Fill documentation gaps",
    "chapter_planner":      "Plan chapters",
    "write_review_chapter": "Write and review chapters",
    "chapter_crossref":     "Cross-reference chapters",
    "chapter_assembler":    "Assemble documentation",
}

# Execution order — used for display ordering and scratchpad restore.
NODE_ORDER: list[str] = [
    "resumption_inspector",
    "intent_parser",
    "web_discovery",
    "confirm_package",
    "docs_discovery",
    "context7_agent",
    "docs_scraper",
    "github_agent",
    "aggregator",
    "quality_judge",
    "enrichment_agent",
    "chapter_planner",
    "write_review_chapter",
    "chapter_crossref",
    "chapter_assembler",
]

STATUS_COLORS: dict[str, str] = {
    "completed":   "🟢",
    "running":     "🔵",
    "in_progress": "🔵",
    "paused":      "🟠",
    "failed":      "🔴",
}

PIPELINE_CSS = """<style>
/* DocSmith pipeline step styles */
[data-testid="stStatusWidget"],
[data-testid="stStatus"],
details[data-testid] {
    min-height: 60px;
    margin-bottom: 4px;
    border-left: 3px solid #378ADD;
    padding-left: 6px;
}
[data-testid="stStatusWidget"] .stCode,
[data-testid="stStatus"] .stCode,
.stCode pre, .stCode code {
    font-size: 13px !important;
}

/* Sidebar session card delete column: centre content and keep button compact */
section[data-testid="stSidebar"]
    [data-testid="stHorizontalBlock"]
    [data-testid="stColumn"]:last-child {
    display: flex;
    align-items: center;
    justify-content: center;
}
section[data-testid="stSidebar"]
    [data-testid="stHorizontalBlock"]
    [data-testid="stColumn"]:last-child
    button {
    width: auto !important;
    min-width: unset !important;
    padding: 0.2rem 0.35rem !important;
    font-size: 1rem !important;
    line-height: 1 !important;
}
</style>"""

# Sentinel used in the HITL package-confirmation radio list.
NONE_OF_THE_ABOVE = "None of the above — I'll specify the package myself"
