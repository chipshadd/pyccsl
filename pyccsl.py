#!/usr/bin/env python3
"""
pyccsl - Python Claude Code Status Line
Generates a customizable status line for Claude Code showing performance metrics,
git status, session information, and cost calculations.

Exit Codes:
    0 - Success
    1 - Configuration/argument error  
    2 - Input/JSON error
    3 - File access error (reserved)
    4 - Runtime/processing error (reserved)
"""

import sys
import json
import os
import subprocess
import time
import math
from datetime import datetime, timedelta, timezone
import argparse

__version__ = "0.9.37"

# Pricing data embedded from https://docs.anthropic.com/en/docs/about-claude/pricing
# All prices in USD per million tokens
PRICING_DATA = {
    "claude-opus-4-1-20250805": {
        "name": "Claude Opus 4.1",
        "input": 15.00,
        "cache_write_5m": 18.75,
        "cache_write_1h": 30.00,
        "cache_read": 1.50,
        "output": 75.00
    },
    "claude-opus-4-20250514": {
        "name": "Claude Opus 4",
        "input": 15.00,
        "cache_write_5m": 18.75,
        "cache_write_1h": 30.00,
        "cache_read": 1.50,
        "output": 75.00
    },
    "claude-sonnet-4-20250514": {
        "name": "Claude Sonnet 4",
        "input": 3.00,
        "cache_write_5m": 3.75,
        "cache_write_1h": 6.00,
        "cache_read": 0.30,
        "output": 15.00
    },
    "claude-3-7-sonnet-20250219": {
        "name": "Claude Sonnet 3.7",
        "input": 3.00,
        "cache_write_5m": 3.75,
        "cache_write_1h": 6.00,
        "cache_read": 0.30,
        "output": 15.00
    },
    "claude-3-5-sonnet-20241022": {
        "name": "Claude Sonnet 3.5",
        "input": 3.00,
        "cache_write_5m": 3.75,
        "cache_write_1h": 6.00,
        "cache_read": 0.30,
        "output": 15.00
    },
    "claude-3-5-sonnet-20240620": {
        "name": "Claude Sonnet 3.5",
        "input": 3.00,
        "cache_write_5m": 3.75,
        "cache_write_1h": 6.00,
        "cache_read": 0.30,
        "output": 15.00
    },
    "claude-3-5-haiku-20241022": {
        "name": "Claude Haiku 3.5",
        "input": 0.80,
        "cache_write_5m": 1.00,
        "cache_write_1h": 1.60,
        "cache_read": 0.08,
        "output": 4.00
    },
    "claude-3-haiku-20240307": {
        "name": "Claude Haiku 3",
        "input": 0.25,
        "cache_write_5m": 0.30,
        "cache_write_1h": 0.50,
        "cache_read": 0.03,
        "output": 1.25
    }
}

# Theme definitions - 256-color ANSI codes
THEMES = {
    "default": {
        "folder": 208,     # Orange
        "git": 39,         # Blue
        "model": 141,      # Purple
        "input": 83,       # Green
        "output": 214,     # Gold
        "cost": 196        # Red
    },
    "solarized": {
        "folder": 136,     # Yellow
        "git": 33,         # Blue
        "model": 61,       # Purple
        "input": 64,       # Green
        "output": 166,     # Orange
        "cost": 160        # Red
    },
    "nord": {
        "folder": 223,     # Light beige
        "git": 109,        # Blue-green
        "model": 139,      # Purple
        "input": 108,      # Green
        "output": 179,     # Light orange
        "cost": 167        # Nord red
    },
    "dracula": {
        "folder": 215,     # Orange
        "git": 117,        # Cyan
        "model": 141,      # Purple
        "input": 84,       # Green
        "output": 222,     # Yellow
        "cost": 203        # Dracula red
    },
    "gruvbox": {
        "folder": 172,     # Orange
        "git": 66,         # Blue
        "model": 132,      # Purple
        "input": 106,      # Green
        "output": 214,     # Yellow
        "cost": 167        # Gruvbox red
    },
    "tokyo": {
        "folder": 203,     # Pink
        "git": 75,         # Blue
        "model": 176,      # Purple
        "input": 115,      # Green
        "output": 221,     # Yellow
        "cost": 197        # Tokyo red
    },
    "catppuccin": {
        "folder": 217,     # Peach
        "git": 117,        # Sky
        "model": 183,      # Mauve
        "input": 120,      # Green
        "output": 223,     # Yellow
        "cost": 210        # Catppuccin red
    },
    "minimal": {
        "folder": 242,     # Gray
        "git": 245,        # Light gray
        "model": 248,      # Lighter gray
        "input": 250,      # Very light gray
        "output": 252,     # Almost white
        "cost": 252        # Almost white (keep for minimal)
    },
    "none": {}             # No colors
}

# ANSI color code helpers
RESET = "\033[0m"
BOLD = "\033[1m"
GRAY_50 = "\033[38;5;244m"  # 50% gray for badge

# Powerline separator
POWERLINE_RIGHT = "\ue0b0"  # Powerline right arrow (requires powerline font)
POWERLINE_THIN = "\ue0b1"   # Powerline thin right arrow, divides the usage panel

# Usage gauges
GAUGE_CELLS = 5
GAUGE_PARTIALS = "\u258f\u258e\u258d\u258c\u258b\u258a\u2589"  # 1/8 through 7/8 of a cell
GAUGE_EMPTY = "\u2591"
LEVEL_COLORS = [82, 220, 196]  # green, yellow, red
PANEL_BG = 236
PANEL_FG = 250
PANEL_DIVIDER_FG = 244
GAUGE_EMPTY_FG = 240
USAGE_FIELDS = ["usage-5h", "usage-week", "usage-fable"]
USAGE_LABELS = {"usage-5h": "5h", "usage-week": "wk", "usage-fable": "Fable"}
CACHE_LOW_MINUTES = 15
CACHE_CRITICAL_MINUTES = 5
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
USAGE_RETRY_SECONDS = 300
USAGE_STALE_SECONDS = 1800
USAGE_TIMEOUT_SECONDS = 5

def apply_color(text, fg_color=None, bg_color=None, bold=False):
    """Apply ANSI color codes to text.
    
    Args:
        text: The text to colorize
        fg_color: Foreground color code (0-255) or None
        bg_color: Background color code (0-255) or None
        bold: Whether to make text bold
        
    Returns:
        Colored text with reset code at end
    """
    if not text:
        return text
        
    codes = []
    if bold:
        codes.append("1")
    if fg_color is not None:
        codes.append(f"38;5;{fg_color}")
    if bg_color is not None:
        codes.append(f"48;5;{bg_color}")
        
    if codes:
        return f"\033[{';'.join(codes)}m{text}{RESET}"
    return text

def get_field_color(field, theme_colors):
    """Get the color for a field based on the theme.
    
    Args:
        field: The field name
        theme_colors: Theme color dictionary
        
    Returns:
        Color code or None
    """
    if not theme_colors:  # none theme
        return None
        
    # Map fields to their color categories
    if field == "badge":
        return 244  # Always 50% gray for badge
    elif field in ["folder"]:
        return theme_colors.get("folder")
    elif field in ["git"]:
        return theme_colors.get("git")
    elif field in ["model", "perf-cache-rate", "perf-response-time", 
                   "perf-session-time", "perf-message-count",
                   "perf-all-metrics"]:
        return theme_colors.get("model")
    elif field in ["input"]:
        return theme_colors.get("input")
    elif field in ["output", "tokens"]:
        return theme_colors.get("output")
    elif field in ["cost"]:
        return theme_colors.get("cost")
    return None

# Default field list
DEFAULT_FIELDS = ["badge", "folder", "git", "model", "tokens", "cost"]

# All available fields in display order
FIELD_ORDER = [
    "badge",
    "folder", 
    "git",
    "model",
    "perf-cache-rate",
    "perf-response-time",
    "perf-session-time",
    "perf-message-count",
    "idle-time",
    "perf-all-metrics",
    "input",
    "output",
    "tokens",
    "cache",
    "cost",
    "usage-5h",
    "usage-week",
    "usage-fable"
]

def parse_env_file(filepath):
    """Parse environment file and return a dictionary of variables.
    
    File format:
    - Lines can be: VAR=value, VAR="value", or VAR='value'
    - Lines starting with # are comments
    - Empty lines are ignored
    - The file is also valid bash script syntax
    
    Returns dict of environment variable names to values.
    """
    env_vars = {}
    
    if not filepath or not os.path.exists(filepath):
        return env_vars
    
    try:
        with open(filepath, 'r') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                
                # Skip empty lines and comments
                if not line or line.startswith('#'):
                    continue
                
                # Parse VAR=value format
                if '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    
                    # Remove surrounding quotes if present
                    if (value.startswith('"') and value.endswith('"')) or \
                       (value.startswith("'") and value.endswith("'")):
                        value = value[1:-1]
                    
                    # Only process PYCCSL_ variables for security
                    if key.startswith('PYCCSL_'):
                        env_vars[key] = value
                    
    except Exception as e:
        # If there's an error reading the file, just continue without it
        sys.stderr.write(f"Warning: Could not read env file {filepath}: {e}\n")
    
    return env_vars

def parse_arguments():
    """Parse command-line arguments."""
    # Debug: print raw sys.argv
    if "--debug" in sys.argv:
        sys.stderr.write(f"DEBUG: sys.argv = {sys.argv}\n")
    
    parser = argparse.ArgumentParser(
        description="Claude Code status line generator",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    # Theme option
    parser.add_argument(
        "--theme",
        choices=["default", "solarized", "nord", "dracula", "gruvbox", 
                 "tokyo", "catppuccin", "minimal", "none"],
        default=os.environ.get("PYCCSL_THEME", "default"),
        help="Color theme (default: default)"
    )
    
    # Number formatting option
    parser.add_argument(
        "--numbers",
        choices=["compact", "full", "raw"],
        default=os.environ.get("PYCCSL_NUMBERS", "compact"),
        help="Number formatting (default: compact)"
    )
    
    # Style option
    parser.add_argument(
        "--style",
        choices=["powerline", "simple", "arrows", "pipes", "dots"],
        default=os.environ.get("PYCCSL_STYLE", "simple"),
        help="Separator style (default: simple)"
    )
    
    # No emoji option
    parser.add_argument(
        "--no-emoji",
        action="store_true",
        default=os.environ.get("PYCCSL_NO_EMOJI", "false").lower() == "true",
        help="Disable emoji in output"
    )
    
    # Debug option
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Output debug information to stderr"
    )
    
    # Environment file option
    parser.add_argument(
        "--env",
        help="Path to environment file with PYCCSL_* variables"
    )
    
    # Performance thresholds - cache
    parser.add_argument(
        "--perf-cache",
        default=os.environ.get("PYCCSL_PERF_CACHE", "95,90,75"),
        help="Cache hit rate thresholds (green,yellow,orange) (default: 95,90,75)"
    )
    
    # Performance thresholds - response
    parser.add_argument(
        "--perf-response",
        default=os.environ.get("PYCCSL_PERF_RESPONSE", "10,30,60"),
        help="Response time thresholds (green,yellow,orange) (default: 10,30,60)"
    )

    # Cache TTL for idle-time field
    parser.add_argument(
        "--cache-ttl",
        type=int,
        default=int(os.environ.get("PYCCSL_CACHE_TTL", "3600")),
        help="Cache TTL in seconds for idle-time warning (default: 3600)"
    )
    
    # Background usage fetch, launched by the status line itself
    parser.add_argument(
        "--fetch-usage",
        action="store_true",
        help=argparse.SUPPRESS
    )

    # Fields to display (positional argument)
    parser.add_argument(
        "fields",
        nargs="?",
        default=os.environ.get("PYCCSL_FIELDS", None),
        help="Comma-separated list of fields to display"
    )
    
    args = parser.parse_args()
    
    # Load environment file if specified
    env_vars = {}
    if args.env:
        env_vars = parse_env_file(args.env)
        if args.debug and env_vars:
            sys.stderr.write(f"DEBUG: Loaded env vars from {args.env}: {list(env_vars.keys())}\n")
    
    # Apply env file overrides (higher priority than command line)
    # This allows dynamic configuration changes by modifying the env file
    if 'PYCCSL_THEME' in env_vars:
        args.theme = env_vars['PYCCSL_THEME']
    if 'PYCCSL_NUMBERS' in env_vars:
        args.numbers = env_vars['PYCCSL_NUMBERS']
    if 'PYCCSL_STYLE' in env_vars:
        args.style = env_vars['PYCCSL_STYLE']
    if 'PYCCSL_NO_EMOJI' in env_vars:
        args.no_emoji = env_vars['PYCCSL_NO_EMOJI'].lower() == 'true'
    if 'PYCCSL_PERF_CACHE' in env_vars:
        args.perf_cache = env_vars['PYCCSL_PERF_CACHE']
    if 'PYCCSL_PERF_RESPONSE' in env_vars:
        args.perf_response = env_vars['PYCCSL_PERF_RESPONSE']
    if 'PYCCSL_CACHE_TTL' in env_vars:
        args.cache_ttl = int(env_vars['PYCCSL_CACHE_TTL'])
    if 'PYCCSL_FIELDS' in env_vars:
        args.fields = env_vars['PYCCSL_FIELDS']
    
    # Parse fields
    if args.fields:
        # Split comma-separated fields and strip whitespace
        fields = [f.strip() for f in args.fields.split(",") if f.strip()]
        # If all fields were empty/whitespace, use defaults
        if not fields:
            if "--debug" in sys.argv:
                sys.stderr.write(f"DEBUG: Empty fields specified, using defaults\n")
            fields = DEFAULT_FIELDS.copy()
    else:
        if "--debug" in sys.argv:
            sys.stderr.write(f"DEBUG: No fields specified, using defaults\n")
        fields = DEFAULT_FIELDS.copy()
    
    # Parse threshold values
    try:
        cache_thresholds = [float(x) for x in args.perf_cache.split(",")]
        if len(cache_thresholds) != 3:
            raise ValueError("Need exactly 3 cache thresholds")
    except (ValueError, AttributeError):
        print("Error: Invalid cache thresholds format. Expected: three comma-separated numbers (e.g., 60,40,20)", file=sys.stderr)
        sys.exit(1)
    
    try:
        response_thresholds = [float(x) for x in args.perf_response.split(",")]
        if len(response_thresholds) != 3:
            raise ValueError("Need exactly 3 response thresholds")
    except (ValueError, AttributeError):
        print("Error: Invalid response thresholds format. Expected: three comma-separated numbers (e.g., 3,5,8)", file=sys.stderr)
        sys.exit(1)
    
    return {
        "theme": args.theme,
        "numbers": args.numbers,
        "style": args.style,
        "no_emoji": args.no_emoji,
        "debug": args.debug,
        "cache_thresholds": cache_thresholds,
        "response_thresholds": response_thresholds,
        "cache_ttl": args.cache_ttl,
        "fields": fields,
        "fetch_usage": args.fetch_usage
    }

def read_input():
    """Read and parse JSON input from stdin."""
    try:
        # Check if stdin has data (not a terminal)
        if sys.stdin.isatty():
            print("Error: No input provided. This script expects JSON data via stdin from Claude Code.", file=sys.stderr)
            sys.exit(2)
        
        # Read from stdin
        input_data = sys.stdin.read()
        
        if not input_data.strip():
            print("Error: Empty input received.", file=sys.stderr)
            sys.exit(2)
        
        # Parse JSON
        try:
            data = json.loads(input_data)
        except json.JSONDecodeError as e:
            print(f"Error: Invalid JSON input: {e}", file=sys.stderr)
            sys.exit(2)
        
        return data
        
    except Exception as e:
        print(f"Error reading input: {e}", file=sys.stderr)
        sys.exit(2)

def extract_model_info(data):
    """Extract model information from input data."""
    try:
        # Try to get model display_name
        if "model" in data and isinstance(data["model"], dict):
            display_name = data["model"].get("display_name", "Unknown")
            model_id = data["model"].get("id", None)
            return {"display_name": display_name, "id": model_id}
        else:
            # Fallback if model not present
            return {"display_name": "Unknown", "id": None}
    except Exception:
        return {"display_name": "Unknown", "id": None}

def extract_git_status(input_data):
    """Extract git status information from the current directory.
    
    Returns a dict with:
    - branch: Current branch name or None
    - modified_count: Number of modified/staged files or 0
    """
    try:
        # Get working directory from input or use current
        cwd = input_data.get("cwd", os.getcwd())
        
        # Get current branch name
        branch_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=2
        )
        
        if branch_result.returncode != 0:
            # Not a git repository
            return {"branch": None, "modified_count": 0}
        
        branch = branch_result.stdout.strip()
        
        # Get modified file count using porcelain format
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=2
        )
        
        # Count non-empty lines (each represents a modified file)
        modified_count = 0
        if status_result.returncode == 0:
            modified_count = len([line for line in status_result.stdout.splitlines() if line.strip()])
        
        return {"branch": branch, "modified_count": modified_count}
        
    except (subprocess.TimeoutExpired, FileNotFoundError):
        # Git not available or timeout
        return {"branch": None, "modified_count": 0}
    except Exception:
        # Any other error - fail silently
        return {"branch": None, "modified_count": 0}

def get_model_pricing(model_id):
    """Get pricing information for a model ID.
    
    Returns a dict with pricing info or None if model not found.
    """
    if model_id and model_id in PRICING_DATA:
        return PRICING_DATA[model_id]
    return None

def load_transcript(transcript_path, debug=False):
    """Load and parse a Claude Code transcript JSONL file.
    
    Args:
        transcript_path: Path to the transcript file
        debug: Whether to output debug information
    
    Returns:
        List of parsed JSON entries, or empty list on error
    """
    if not transcript_path:
        if debug:
            sys.stderr.write(f"DEBUG: No transcript path provided\n")
        return []
    
    if not os.path.exists(transcript_path):
        if debug:
            sys.stderr.write(f"DEBUG: Transcript file does not exist: {transcript_path}\n")
        return []
    
    try:
        entries = []
        with open(transcript_path, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue  # Skip empty lines
                try:
                    entry = json.loads(line)
                    entries.append(entry)
                except json.JSONDecodeError as e:
                    # Log error but continue processing other lines
                    print(f"Warning: Invalid JSON at line {line_num} in transcript: {e}", file=sys.stderr)
                    continue
        if debug:
            sys.stderr.write(f"DEBUG: Successfully loaded {len(entries)} transcript entries\n")
        return entries
    except FileNotFoundError:
        # Transcript file not found - this is expected for new sessions
        if debug:
            sys.stderr.write(f"DEBUG: Transcript file not found: {transcript_path}\n")
        return []
    except (PermissionError, IOError) as e:
        # File access errors
        if debug:
            sys.stderr.write(f"DEBUG: Cannot access transcript file: {e}\n")
        return []
    except Exception as e:
        # Other unexpected errors
        if debug:
            sys.stderr.write(f"DEBUG: Unexpected error reading transcript: {e}\n")
        return []

def calculate_token_usage(transcript_entries):
    """Calculate total token usage from transcript entries.
    
    Args:
        transcript_entries: List of parsed transcript entries
    
    Returns:
        Dict with token totals: input_tokens, output_tokens, 
        cache_creation_tokens, cache_read_tokens
    """
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0
    }
    
    for entry in transcript_entries:
        usage = None
        
        # Check for usage in assistant messages
        if entry.get("type") == "assistant" and "message" in entry:
            usage = entry["message"].get("usage", {})
        
        # Check for usage in tool use results (only if it's a dict)
        elif "toolUseResult" in entry and isinstance(entry["toolUseResult"], dict):
            usage = entry["toolUseResult"].get("usage", {})
        
        if usage:
            # Add tokens to totals
            totals["input_tokens"] += usage.get("input_tokens", 0)
            totals["output_tokens"] += usage.get("output_tokens", 0)
            totals["cache_creation_tokens"] += usage.get("cache_creation_input_tokens", 0)
            totals["cache_read_tokens"] += usage.get("cache_read_input_tokens", 0)
    
    return totals

def get_last_usage(transcript_entries):
    """Get usage from the last transcript entry that has usage data.

    The API reports cumulative context in each response, so the last
    entry reflects the current context window size.

    Args:
        transcript_entries: List of parsed transcript entries

    Returns:
        Dict with usage data from the last entry, or empty dict
    """
    last_usage = None

    for entry in transcript_entries:
        usage = None
        if entry.get("type") == "assistant" and "message" in entry:
            usage = entry["message"].get("usage", {})
        elif "toolUseResult" in entry and isinstance(entry["toolUseResult"], dict):
            usage = entry["toolUseResult"].get("usage", {})

        if usage and usage.get("input_tokens", 0) > 0:
            last_usage = usage

    return last_usage or {}

def get_model_from_transcript(transcript_entries):
    """Extract the model ID from transcript entries.
    
    Looks for the first assistant message with a model ID.
    
    Args:
        transcript_entries: List of parsed transcript entries
    
    Returns:
        Model ID string or None if not found
    """
    for entry in transcript_entries:
        if entry.get("type") == "assistant" and "message" in entry:
            model_id = entry["message"].get("model")
            if model_id:
                return model_id
    return None

def calculate_cost_per_entry(usage, model_id):
    """Calculate cost for a single entry based on its token usage and model.
    
    Args:
        usage: Dict with token usage for this entry
        model_id: Model ID string for pricing lookup
    
    Returns:
        Cost in dollars (float) or 0.0 if model not found
    """
    pricing = get_model_pricing(model_id)
    if not pricing:
        return 0.0
    
    # Calculate cost using the formula (all rates are per million tokens)
    # Using 5-minute cache write rate (Claude Code default)
    cost = (
        usage.get("input_tokens", 0) * pricing.get("input", 0) +
        usage.get("cache_creation_input_tokens", 0) * pricing.get("cache_write_5m", 0) +
        usage.get("cache_read_input_tokens", 0) * pricing.get("cache_read", 0) +
        usage.get("output_tokens", 0) * pricing.get("output", 0)
    ) / 1_000_000
    
    return cost

def calculate_total_cost(transcript_entries, debug=False):
    """Calculate total session cost by summing per-entry costs using each entry's model.
    
    Args:
        transcript_entries: List of parsed transcript entries
        debug: Whether to output debug information
    
    Returns:
        Total cost in dollars (float)
    """
    total_cost = 0.0
    model_costs = {}  # Track costs per model for debugging
    
    # Build UUID lookup for finding parent models
    uuid_lookup = {}
    for entry in transcript_entries:
        uuid = entry.get("uuid")
        if uuid:
            uuid_lookup[uuid] = entry
    
    # Track the last seen model for tool results that don't specify one
    last_model_id = None
    
    for entry in transcript_entries:
        usage = None
        model_id = None
        
        # Check for usage and model in assistant messages
        if entry.get("type") == "assistant" and "message" in entry:
            message = entry["message"]
            usage = message.get("usage", {})
            model_info = message.get("model")
            
            # Handle both string and dict formats for model
            if isinstance(model_info, dict):
                model_id = model_info.get("id")
            else:
                model_id = model_info
                
            if model_id:
                last_model_id = model_id  # Remember this model
        
        # Check for usage in tool use results
        elif "toolUseResult" in entry and isinstance(entry["toolUseResult"], dict):
            usage = entry["toolUseResult"].get("usage", {})
            
            # Try to find model from parent assistant message
            parent_uuid = entry.get("parentUuid")
            if parent_uuid and parent_uuid in uuid_lookup:
                parent = uuid_lookup[parent_uuid]
                if parent.get("type") == "assistant" and "message" in parent:
                    parent_model = parent["message"].get("model")
                    # Extract model ID from dict if needed
                    if isinstance(parent_model, dict):
                        model_id = parent_model.get("id")
                    else:
                        model_id = parent_model
            
            # Fallback to last seen model if parent lookup fails
            if not model_id:
                model_id = last_model_id
        
        if usage and model_id:
            entry_cost = calculate_cost_per_entry(usage, model_id)
            total_cost += entry_cost
            
            # Track per-model costs for debugging
            if model_id not in model_costs:
                model_costs[model_id] = 0.0
            model_costs[model_id] += entry_cost
        elif usage and not model_id and debug:
            # Log entries with usage but no model
            sys.stderr.write(f"DEBUG: Entry with usage but no model: {entry.get('uuid', 'unknown')[:8]}\n")
    
    if debug and model_costs:
        sys.stderr.write(f"DEBUG: Cost breakdown by model:\n")
        for model_id, cost in model_costs.items():
            sys.stderr.write(f"DEBUG:   {model_id}: ${cost:.4f}\n")
        sys.stderr.write(f"DEBUG:   Total: ${total_cost:.4f}\n")
    
    return total_cost

def format_cost(cost):
    """Format cost as dollars or cents.
    
    Args:
        cost: Cost in dollars (float)
    
    Returns:
        Formatted string (e.g., "$1.25" or "48¢")
    """
    if cost >= 1.0:
        return f"${cost:.2f}"
    else:
        cents = int(round(cost * 100))
        return f"{cents}¢"

def format_number(value, style="compact"):
    """Format a number based on style preference.
    
    Args:
        value: Number to format
        style: "compact" (1.2K), "full" (1,234), or "raw" (1234)
    
    Returns:
        Formatted string
    """
    if style == "compact":
        if value >= 1_000_000:
            return f"{value/1_000_000:.1f}M"
        elif value >= 1_000:
            return f"{value/1_000:.1f}K"
        else:
            return str(value)
    elif style == "full":
        return f"{value:,}"
    else:  # raw
        return str(value)

def calculate_performance_badge(cache_hit_rate, avg_response_time, cache_thresholds, response_thresholds, colored=False, powerline=False, no_emoji=False):
    """Calculate performance badge based on metrics and thresholds.
    
    Args:
        cache_hit_rate: Cache hit rate (0.0 to 1.0)
        avg_response_time: Average response time in seconds
        cache_thresholds: List of [green, yellow, orange] thresholds for cache hit rate
        response_thresholds: List of [green, yellow, orange] thresholds for response time
        colored: Whether to apply colors to the badge
        powerline: Whether this is for powerline style (different coloring)
    
    Returns:
        Badge string (e.g., "●○○○", "○●○○", "○○●○", "○○○●")
    """
    # Calculate performance level for cache hit rate (higher is better)
    cache_percent = cache_hit_rate * 100
    if cache_percent >= cache_thresholds[0]:
        cache_level = 0  # Green
    elif cache_percent >= cache_thresholds[1]:
        cache_level = 1  # Yellow
    elif cache_percent >= cache_thresholds[2]:
        cache_level = 2  # Orange
    else:
        cache_level = 3  # Red
    
    # Calculate performance level for response time (lower is better)
    if avg_response_time <= response_thresholds[0]:
        response_level = 0  # Green
    elif avg_response_time <= response_thresholds[1]:
        response_level = 1  # Yellow
    elif avg_response_time <= response_thresholds[2]:
        response_level = 2  # Orange
    else:
        response_level = 3  # Red
    
    # Combine metrics (take the worse of the two)
    overall_level = max(cache_level, response_level)
    
    # Choose characters based on emoji preference and color availability
    active_char = "*" if no_emoji else "●"
    # When no colors, use open circles for inactive so they're distinguishable
    # When colors are used, use filled circles for both (distinguished by color)
    inactive_char = "o" if no_emoji else ("○" if not colored else "●")
    
    # Generate badge string with colors if requested
    if colored:
        # Define colors for each level: green, darker yellow, orange, red
        colors = [82, 220, 208, 196]  # ANSI 256-color codes (220 is darker yellow)
        
        if powerline:
            # Powerline style: white background throughout, black dots except for the active one
            # Build the entire string with continuous white background
            result = "\033[48;5;244m"  # Start with 50% gray background
            for i in range(4):
                if i == overall_level:
                    # Active dot - just change foreground color, keep white background
                    result += f"\033[38;5;{colors[i]}m{active_char}"
                else:
                    # Inactive dot - black foreground, keep white background
                    result += f"\033[38;5;0m{inactive_char}"
            # Don't add RESET here - let the powerline handler manage that
            return result
        else:
            # Regular style: colored active dot, gray inactive dots
            gray = 244  # Gray for inactive dots
            dots = []
            for i in range(4):
                if i == overall_level:
                    # Active dot with its color
                    dots.append(apply_color(active_char, fg_color=colors[i]))
                else:
                    # Inactive dot - gray outline
                    dots.append(apply_color(inactive_char, fg_color=gray))
            return "".join(dots)
    else:
        # Plain badge without colors
        badges = [
            f"{active_char}{inactive_char}{inactive_char}{inactive_char}",
            f"{inactive_char}{active_char}{inactive_char}{inactive_char}",
            f"{inactive_char}{inactive_char}{active_char}{inactive_char}",
            f"{inactive_char}{inactive_char}{inactive_char}{active_char}"
        ]
        return badges[overall_level]

def calculate_performance_metrics(transcript_entries, token_totals, debug=False):
    """Calculate performance metrics from transcript.
    
    Args:
        transcript_entries: List of parsed transcript entries
        token_totals: Dict with token usage totals
        debug: Whether to output debug information
    
    Returns:
        Dict with performance metrics
    """
    metrics = {}
    
    # Calculate cache hit rate
    total_input = (token_totals.get("input_tokens", 0) + 
                   token_totals.get("cache_creation_tokens", 0) + 
                   token_totals.get("cache_read_tokens", 0))
    if total_input > 0:
        cache_hit_rate = token_totals.get("cache_read_tokens", 0) / total_input
        metrics["cache_hit_rate"] = cache_hit_rate
    else:
        metrics["cache_hit_rate"] = 0.0
    
    # Build UUID lookup map for parent-child relationships
    uuid_lookup = {}
    for entry in transcript_entries:
        uuid = entry.get("uuid")
        if uuid:
            uuid_lookup[uuid] = entry
    
    if debug:
        sys.stderr.write(f"DEBUG: Built UUID lookup with {len(uuid_lookup)} entries\n")
    
    # Calculate response times and count messages
    user_timestamps = []
    assistant_timestamps = []
    response_times = []
    
    # Process entries to collect basic metrics
    for entry in transcript_entries:
        timestamp_str = entry.get("timestamp")
        if not timestamp_str:
            continue
            
        try:
            # Parse ISO format timestamp
            timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        except:
            continue
            
        entry_type = entry.get("type")
        
        # Track user timestamps
        if entry_type == "user":
            user_timestamps.append(timestamp)
        
        # Track assistant timestamps
        elif entry_type == "assistant":
            assistant_timestamps.append(timestamp)
    
    
    # Average response time (simplified - just based on consecutive user/assistant pairs)
    if user_timestamps and assistant_timestamps:
        # Match user and assistant messages in order
        paired_responses = []
        for i, assistant_ts in enumerate(assistant_timestamps):
            # Find the most recent user timestamp before this assistant
            prior_users = [u for u in user_timestamps if u < assistant_ts]
            if prior_users:
                user_ts = max(prior_users)
                response_time = (assistant_ts - user_ts).total_seconds()
                if 0 < response_time < 300:  # Sanity check: between 0 and 5 minutes
                    paired_responses.append(response_time)
        
        if paired_responses:
            metrics["avg_response_time"] = sum(paired_responses) / len(paired_responses)
        else:
            metrics["avg_response_time"] = 0.0
    else:
        metrics["avg_response_time"] = 0.0
    
    # Message count
    metrics["message_count"] = len(user_timestamps)

    # Session duration
    all_timestamps = user_timestamps + assistant_timestamps
    if len(all_timestamps) >= 2:
        all_timestamps.sort()
        session_duration = (all_timestamps[-1] - all_timestamps[0]).total_seconds()
        metrics["session_duration"] = session_duration
    else:
        metrics["session_duration"] = 0.0

    # Idle time since last assistant response (proxy for cache freshness)
    if assistant_timestamps:
        last_api_ts = max(assistant_timestamps)
        # Normalize to UTC if timezone-naive
        if last_api_ts.tzinfo is None:
            last_api_ts = last_api_ts.replace(tzinfo=timezone.utc)
        now_utc = datetime.now(tz=timezone.utc)
        idle = (now_utc - last_api_ts).total_seconds()
        metrics["idle_seconds"] = max(0.0, idle)

    return metrics

def format_duration(seconds):
    """Format duration in seconds to human-readable format."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        minutes = seconds / 60
        return f"{minutes:.0f}m"
    else:
        hours = seconds / 3600
        if hours < 24:
            return f"{hours:.1f}h"
        else:
            days = hours / 24
            return f"{days:.0f}d"

def render_gauge(percent):
    """Render percent as a bar of GAUGE_CELLS cells with eighth-block precision."""
    clamped = max(0.0, min(100.0, float(percent)))
    eighths = int(round(clamped / 100 * GAUGE_CELLS * 8))
    if eighths == 0 and round(percent) >= 1:
        eighths = 1
    full, remainder = divmod(eighths, 8)
    bar = "█" * full + (GAUGE_PARTIALS[remainder - 1] if remainder else "")
    return bar + GAUGE_EMPTY * (GAUGE_CELLS - len(bar))

def usage_level(percent):
    """Return 0 (green), 1 (yellow) or 2 (red) for a usage percent."""
    rounded = round(percent)
    if rounded >= 90:
        return 2
    if rounded >= 75:
        return 1
    return 0

def usage_percentages(input_data):
    """Map usage-5h and usage-week to the stdin rate_limits percents, skipping missing windows."""
    limits = input_data.get("rate_limits")
    if not isinstance(limits, dict):
        return {}
    values = {}
    for field, key in (("usage-5h", "five_hour"), ("usage-week", "seven_day")):
        window = limits.get(key)
        percent = window.get("used_percentage") if isinstance(window, dict) else None
        if is_percent(percent):
            values[field] = percent
    return values

def render_usage(label, percent, mode):
    """Render one usage field as 'label gauge percent'.

    mode is "none" (no color codes), "fg" (foreground colors, each run reset)
    or "panel" (foreground-only codes that restore PANEL_FG, so the powerline
    panel background survives).
    """
    gauge = render_gauge(percent)
    filled = gauge.rstrip(GAUGE_EMPTY)
    empty = gauge[len(filled):]
    shown = f"{round(percent)}%"
    if mode == "none":
        return f"{label} {gauge} {shown}"
    level_fg = LEVEL_COLORS[usage_level(percent)]

    def paint(text, fg):
        if not text:
            return ""
        if mode == "panel":
            return f"\033[38;5;{fg}m{text}\033[38;5;{PANEL_FG}m"
        return apply_color(text, fg_color=fg)

    return f"{label} {paint(filled, level_fg)}{paint(empty, GAUGE_EMPTY_FG)} {paint(shown, level_fg)}"

def group_joiner(bg_color):
    """Separator between fields that share a powerline segment."""
    if bg_color == PANEL_BG:
        return f" \033[38;5;{PANEL_DIVIDER_FG}m{POWERLINE_THIN}\033[38;5;{PANEL_FG}m "
    return " "

def cache_state(prompt_cache, now):
    """Return (state, minutes_left) for the cache field, or None to hide it.

    state is "warm", "low", "critical" or "cold"; minutes_left is None when cold.
    """
    if not isinstance(prompt_cache, dict):
        return None
    if prompt_cache.get("warm") is False:
        return ("cold", None)
    expires_at = prompt_cache.get("expires_at")
    if not prompt_cache.get("warm") or not isinstance(expires_at, (int, float)):
        return None
    remaining = expires_at - now
    if remaining <= 0:
        return ("cold", None)
    minutes = int(remaining // 60)
    if minutes <= CACHE_CRITICAL_MINUTES:
        return ("critical", minutes)
    if minutes <= CACHE_LOW_MINUTES:
        return ("low", minutes)
    return ("warm", minutes)

def format_cache_text(state, minutes, recache_tokens, no_emoji):
    """Text for the cache field."""
    if state == "cold":
        tokens = format_number(recache_tokens, "compact") if isinstance(recache_tokens, (int, float)) else None
        if no_emoji:
            return f"Cache: cold ({tokens})" if tokens else "Cache: cold"
        return f"🧊 cold · {tokens} re-ingest" if tokens else "🧊 cold"
    left = "<1m" if minutes == 0 else f"{minutes}m"
    return f"Cache: {left} left" if no_emoji else f"⏳ {left} left"

def cache_color(state, theme_colors):
    """The theme's input color while warm, then yellow, then red."""
    if not theme_colors:
        return None
    if state == "warm":
        return theme_colors.get("input")
    if state == "low":
        return LEVEL_COLORS[1]
    return LEVEL_COLORS[2]

def usage_cache_path():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "pyccsl", "usage.json")

def credentials_path():
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    return os.path.join(base, ".credentials.json")

def read_json_file(path):
    """Return the JSON object in path, or {} when it is missing or unreadable."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def write_json_atomic(path, data):
    """Write data to path through a temp file and os.replace. Returns True on success."""
    import tempfile
    try:
        directory = os.path.dirname(path)
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".usage-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp_path, path)
        except Exception:
            os.unlink(tmp_path)
            raise
        return True
    except Exception:
        return False

def is_percent(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

def fable_from_cache(cache, now):
    """The cached Fable percent, or None when it is missing or stale."""
    fetched_at = cache.get("fetched_at")
    fable = cache.get("fable")
    if not isinstance(fetched_at, (int, float)) or not isinstance(fable, dict):
        return None
    if not 0 <= now - fetched_at <= USAGE_STALE_SECONDS:
        return None
    percent = fable.get("percent")
    return percent if is_percent(percent) else None

def spawn_fetch():
    """Launch `pyccsl.py --fetch-usage` fully detached from the status line's pipes."""
    subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "--fetch-usage"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )

def maybe_start_fetch(cache_path, cache, now, spawn):
    """Record an attempt and launch a fetch when the last attempt is stale.

    Returns True when a fetch was launched. A failed write launches nothing.
    """
    attempted_at = cache.get("attempted_at")
    if isinstance(attempted_at, (int, float)) and 0 <= now - attempted_at <= USAGE_RETRY_SECONDS:
        return False
    stamped = dict(cache)
    stamped["attempted_at"] = now
    if not write_json_atomic(cache_path, stamped):
        return False
    spawn()
    return True

def extract_fable(payload):
    """The Fable weekly window from a usage response, as {"percent", "resets_at"}, or None."""
    limits = payload.get("limits") if isinstance(payload, dict) else None
    if not isinstance(limits, list):
        return None
    for entry in limits:
        if not isinstance(entry, dict) or entry.get("kind") != "weekly_scoped":
            continue
        scope = entry.get("scope")
        model = scope.get("model") if isinstance(scope, dict) else None
        name = model.get("display_name") if isinstance(model, dict) else None
        percent = entry.get("percent")
        if isinstance(name, str) and name.lower() == "fable" and is_percent(percent):
            return {"percent": percent, "resets_at": entry.get("resets_at")}
    return None

def http_get_json(url, headers, timeout):
    import urllib.request

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    request = urllib.request.Request(url, headers=headers)
    with urllib.request.build_opener(NoRedirect).open(request, timeout=timeout) as response:
        return json.load(response)

def run_fetch_usage(cache_path, creds_path, now, get_json=http_get_json):
    """Fetch the usage endpoint once and record the Fable window. Never raises.

    Only the access token is read; refreshing would rotate the refresh token
    and can log Claude Code out.
    """
    try:
        oauth = read_json_file(creds_path).get("claudeAiOauth") or {}
        token = oauth.get("accessToken")
        expires_at_ms = oauth.get("expiresAt")
        if not token:
            return
        if isinstance(expires_at_ms, (int, float)) and expires_at_ms / 1000 <= now:
            return
        payload = get_json(USAGE_URL, {
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
            "Content-Type": "application/json",
        }, USAGE_TIMEOUT_SECONDS)
        fable = extract_fable(payload)
        if fable is None:
            return
        cache = read_json_file(cache_path)
        cache["fetched_at"] = now
        cache["fable"] = fable
        write_json_atomic(cache_path, cache)
    except Exception:
        return

def collect_cache_and_usage(input_data, fields, now, spawn=spawn_fetch):
    """Metrics for the cache and usage fields. Never raises."""
    metrics = {}
    try:
        prompt_cache = input_data.get("prompt_cache")
        state = cache_state(prompt_cache, now)
        if state:
            metrics["cache_state"] = state
            tokens = prompt_cache.get("recache_tokens_if_cold")
            metrics["cache_recache_tokens"] = tokens if is_percent(tokens) else None
    except Exception:
        pass
    usage = {}
    try:
        usage = usage_percentages(input_data)
    except Exception:
        pass
    if "usage-fable" in fields and isinstance(input_data.get("rate_limits"), dict):
        try:
            path = usage_cache_path()
            cache = read_json_file(path)
            fable = fable_from_cache(cache, now)
            if fable is not None:
                usage["usage-fable"] = fable
            maybe_start_fetch(path, cache, now, spawn)
        except Exception:
            pass
    metrics["usage"] = usage
    return metrics

def format_output(config, model_info, input_data, metrics=None):
    """Format the output based on selected fields and configuration.
    
    Args:
        config: Configuration dict from parse_arguments()
        model_info: Model information dict with display_name and id
        input_data: Full input JSON data
        metrics: Dict with calculated metrics (cost, tokens, etc.)
    
    Returns:
        Formatted string for output
    """
    if metrics is None:
        metrics = {}
    
    debug = config.get("debug", False)
    
    if debug:
        sys.stderr.write(f"DEBUG: === format_output ===\n")
        sys.stderr.write(f"DEBUG: Requested fields: {config['fields']}\n")
        sys.stderr.write(f"DEBUG: Available metrics: {list(metrics.keys())}\n")
        if not metrics:
            sys.stderr.write(f"DEBUG: WARNING: metrics dict is empty!\n")
        sys.stderr.write(f"DEBUG: Model info: {model_info}\n")
    
    # Get theme colors
    theme_colors = THEMES.get(config["theme"], {})
    is_powerline = config["style"] == "powerline"
    
    # Initialize storage based on style
    output_parts = []  # Used for non-powerline or powerline without theme
    segments = []  # Used for powerline with theme
    
    # Get separator based on style (for non-powerline)
    if config["style"] == "pipes":
        separator = " | "
    elif config["style"] == "arrows":
        separator = " → "
    elif config["style"] == "dots":
        separator = " · "
    else:  # simple or powerline fallback
        separator = " > "
    
    # Process fields in FIELD_ORDER sequence
    for field in FIELD_ORDER:
        if field not in config["fields"]:
            continue
            
        if debug:
            sys.stderr.write(f"DEBUG: Processing field: {field}\n")
            
        # Prepare field content
        field_content = None
        
        # Handle different fields
        if field == "badge":
            if "badge" in metrics:
                field_content = metrics["badge"]
            elif debug:
                sys.stderr.write(f"DEBUG: Badge not in metrics (need cache_hit_rate and avg_response_time)\n")
        elif field == "model":
            if "display_name" in model_info:
                field_content = model_info["display_name"]
            elif debug:
                sys.stderr.write(f"DEBUG: No display_name in model_info: {model_info}\n")
        elif field == "folder":
            # Extract folder name from cwd
            cwd = input_data.get("cwd", os.getcwd())
            folder_name = os.path.basename(cwd)
            # Handle root directory
            if not folder_name:
                folder_name = "/" if cwd == "/" else os.path.basename(os.path.dirname(cwd))
            # Truncate if too long
            if len(folder_name) > 20:
                folder_name = folder_name[:17] + "..."
            field_content = folder_name
        elif field == "input" and any(k in metrics for k in ["input_tokens", "cache_creation_tokens", "cache_read_tokens"]):
            # Display as tuple: (base, cache_write, cache_read)
            base = format_number(metrics.get("input_tokens", 0), config["numbers"])
            cache_write = format_number(metrics.get("cache_creation_tokens", 0), config["numbers"])
            cache_read = format_number(metrics.get("cache_read_tokens", 0), config["numbers"])
            prefix = "In:" if config["no_emoji"] else "↑"
            field_content = f"{prefix} ({base}, {cache_write}, {cache_read})"
        elif field == "output" and "output_tokens" in metrics:
            prefix = "Out:" if config["no_emoji"] else "↓"
            field_content = f"{prefix} {format_number(metrics['output_tokens'], config['numbers'])}"
        elif field == "tokens" and "context_size" in metrics:
            prefix = "Tok:" if config["no_emoji"] else "⧉"
            field_content = f"{prefix} {format_number(metrics['context_size'], config['numbers'])}"
        elif field == "cost":
            if "cost_formatted" in metrics:
                field_content = metrics["cost_formatted"]
            elif debug:
                sys.stderr.write(f"DEBUG: Cost not available - need model_id and token data\n")
        elif field == "git" and "git_info" in metrics:
            # Format git status: "branch ●" if modified, "branch" if clean
            branch = metrics["git_info"]["branch"]
            modified = metrics["git_info"]["modified_count"]
            if modified > 0:
                indicator = "*" if config["no_emoji"] else "●"
                field_content = f"{branch} {indicator}"
            else:
                field_content = branch
        elif field == "perf-cache-rate" and "cache_hit_rate" in metrics:
            # Format cache hit rate as percentage
            rate = metrics["cache_hit_rate"] * 100
            if config["no_emoji"]:
                field_content = f"Cache: {rate:.0f}%"
            else:
                field_content = f"⚡ {rate:.0f}%"
        elif field == "perf-response-time" and "avg_response_time" in metrics:
            # Format average response time
            time_str = format_duration(metrics["avg_response_time"])
            if config["no_emoji"]:
                field_content = f"Response: {time_str}"
            else:
                field_content = f"⏱ {time_str}"
        elif field == "perf-session-time" and "session_duration" in metrics:
            # Format session duration
            time_str = format_duration(metrics["session_duration"])
            if config["no_emoji"]:
                field_content = f"Session: {time_str}"
            else:
                field_content = f"🕐 {time_str}"
        elif field == "perf-message-count" and "message_count" in metrics:
            # Format message count
            count = metrics["message_count"]
            if config["no_emoji"]:
                field_content = f"Messages: {count}"
            else:
                field_content = f"💬 {count}"
        elif field == "idle-time" and "idle_seconds" in metrics:
            idle = metrics["idle_seconds"]
            time_str = format_duration(idle)
            over_ttl = idle >= config["cache_ttl"]
            if config["no_emoji"]:
                prefix = "CACHE EXPIRED:" if over_ttl else "Idle:"
            else:
                prefix = "⚠️" if over_ttl else "💤"
            field_content = f"{prefix} {time_str}"
        elif field == "perf-all-metrics":
            # Show all performance metrics together
            perf_parts = []
            if "cache_hit_rate" in metrics:
                rate = metrics["cache_hit_rate"] * 100
                perf_parts.append(f"⚡ {rate:.0f}%" if not config["no_emoji"] else f"Cache: {rate:.0f}%")
            if "avg_response_time" in metrics:
                time_str = format_duration(metrics["avg_response_time"])
                perf_parts.append(f"⏱ {time_str}" if not config["no_emoji"] else f"Response: {time_str}")
            if "session_duration" in metrics:
                time_str = format_duration(metrics["session_duration"])
                perf_parts.append(f"🕐 {time_str}" if not config["no_emoji"] else f"Session: {time_str}")
            if "message_count" in metrics:
                count = metrics["message_count"]
                perf_parts.append(f"💬 {count}" if not config["no_emoji"] else f"Messages: {count}")
            if "idle_seconds" in metrics:
                idle = metrics["idle_seconds"]
                time_str = format_duration(idle)
                over_ttl = idle >= config["cache_ttl"]
                if config["no_emoji"]:
                    perf_parts.append(f"CACHE EXPIRED: {time_str}" if over_ttl else f"Idle: {time_str}")
                else:
                    emoji = "⚠️" if over_ttl else "💤"
                    perf_parts.append(f"{emoji} {time_str}")
            if perf_parts:
                field_content = " ".join(perf_parts)
        elif field == "cache" and "cache_state" in metrics:
            state, minutes = metrics["cache_state"]
            field_content = format_cache_text(state, minutes, metrics.get("cache_recache_tokens"), config["no_emoji"])
        elif field in USAGE_FIELDS and field in metrics.get("usage", {}):
            if config["theme"] == "none":
                mode = "none"
            elif is_powerline:
                mode = "panel"
            else:
                mode = "fg"
            field_content = render_usage(USAGE_LABELS[field], metrics["usage"][field], mode)

        # Add field to output
        if field_content:
            if debug:
                sys.stderr.write(f"DEBUG: Adding field '{field}' with content: '{field_content[:50]}...'\n")
            
            if is_powerline and config["theme"] != "none":
                # For powerline, store segment with its background color
                bg_color = get_field_color(field, theme_colors)
                if field == "badge":
                    # Badge gets 50% gray background in powerline mode for better contrast
                    bg_color = 244  # 50% gray
                elif field == "cache":
                    bg_color = cache_color(metrics["cache_state"][0], theme_colors)
                elif field in USAGE_FIELDS:
                    bg_color = PANEL_BG
                segments.append((field_content, bg_color))
            else:
                # Regular styling - apply foreground color
                if field == "cache":
                    color = cache_color(metrics["cache_state"][0], theme_colors)
                elif field != "badge" and field not in USAGE_FIELDS:
                    color = get_field_color(field, theme_colors)
                else:
                    color = None
                if color is not None:
                    field_content = apply_color(field_content, fg_color=color)
                output_parts.append(field_content)
        else:
            if debug:
                sys.stderr.write(f"DEBUG: Field '{field}' has no content, skipping\n")
    
    # Build final output
    if is_powerline and config["theme"] != "none":
        # Build powerline style output
        # Group segments by background color
        grouped_segments = []
        current_group = []
        current_bg = None
        
        for text, bg_color in segments:
            if bg_color == current_bg and current_bg is not None:
                # Same background, add to current group
                current_group.append(text)
            else:
                # New background, save previous group and start new one
                if current_group:
                    grouped_segments.append((group_joiner(current_bg).join(current_group), current_bg))
                current_group = [text]
                current_bg = bg_color
        
        # Add final group
        if current_group:
            grouped_segments.append((group_joiner(current_bg).join(current_group), current_bg))
        
        # Build result with grouped segments
        result = []
        for i, (text, bg_color) in enumerate(grouped_segments):
            if bg_color is None:
                continue
            
            # Apply the segment's background and text colors
            # Badge is now treated the same as all other fields - simple and consistent
            text_fg = PANEL_FG if bg_color == PANEL_BG else 0
            segment_text = apply_color(f" {text} ", fg_color=text_fg, bg_color=bg_color)
            result.append(segment_text)
            
            # Add separator if not last segment
            if i < len(grouped_segments) - 1:
                next_bg = grouped_segments[i + 1][1] if i + 1 < len(grouped_segments) else None
                if next_bg is not None:
                    # Separator with current bg as fg, next bg as bg
                    sep = apply_color(POWERLINE_RIGHT, fg_color=bg_color, bg_color=next_bg)
                else:
                    # Final separator with just fg color
                    sep = apply_color(POWERLINE_RIGHT, fg_color=bg_color)
                result.append(sep)
            else:
                # Final separator - foreground matches last segment bg, background is default (0 = black)
                sep = apply_color(POWERLINE_RIGHT, fg_color=bg_color, bg_color=0)
                result.append(sep)
        
        result_str = "".join(result)
        if debug:
            sys.stderr.write(f"DEBUG: Returning powerline output with {len(segments)} segments\n")
        return result_str
    elif is_powerline:
        # Powerline with no colors - use simple separator
        result_str = " > ".join(output_parts)
        if debug:
            sys.stderr.write(f"DEBUG: Returning powerline (no theme) output with {len(output_parts)} parts\n")
        return result_str
    else:
        # Join parts with separator for non-powerline styles
        result_str = separator.join(output_parts)
        if debug:
            sys.stderr.write(f"DEBUG: Returning regular output with {len(output_parts)} parts\n")
        return result_str

def main():
    """Main entry point."""
    # Parse arguments
    config = parse_arguments()
    debug = config.get("debug", False)

    if config["fetch_usage"]:
        run_fetch_usage(usage_cache_path(), credentials_path(), int(time.time()))
        return 0

    if debug:
        sys.stderr.write(f"DEBUG: Config: {config}\n")
    
    # Read input
    input_data = read_input()
    
    if debug:
        sys.stderr.write(f"DEBUG: Input data keys: {list(input_data.keys())}\n")
        sys.stderr.write(f"DEBUG: Model: {input_data.get('model', 'None')}\n")
        sys.stderr.write(f"DEBUG: Transcript path: {input_data.get('transcript_path', 'None')}\n")
        sys.stderr.write(f"DEBUG: CWD: {input_data.get('cwd', 'None')}\n")
    
    # Extract model info
    model_info = extract_model_info(input_data)
    
    if debug:
        sys.stderr.write(f"DEBUG: Model info: {model_info}\n")
    
    # Extract git status
    git_info = extract_git_status(input_data)
    
    if debug:
        sys.stderr.write(f"DEBUG: Git info: {git_info}\n")
    
    # Load transcript if provided
    transcript_path = input_data.get("transcript_path", None)
    transcript_entries = load_transcript(transcript_path, debug=debug)
    
    # Calculate metrics from transcript
    metrics = {}
    
    if debug:
        sys.stderr.write(f"DEBUG: Transcript entries loaded: {len(transcript_entries)}\n")
    
    if transcript_entries:
        # Calculate token usage
        token_totals = calculate_token_usage(transcript_entries)
        metrics.update(token_totals)
        
        if debug:
            sys.stderr.write(f"DEBUG: Token totals: {token_totals}\n")
        
        # Context size = input side of the last API call (includes cache_read)
        # Each API response reports the full context at that point, so only
        # the last entry matters. Cache-read tokens still occupy context space.
        # Output tokens are excluded since they haven't been fed back as input yet.
        last_usage = get_last_usage(transcript_entries)
        context_size = (last_usage.get("input_tokens", 0) +
                       last_usage.get("cache_creation_input_tokens", 0) +
                       last_usage.get("cache_read_input_tokens", 0))
        metrics["context_size"] = context_size
        
        # Calculate cost using per-entry models
        cost = calculate_total_cost(transcript_entries, debug=debug)
        metrics["cost"] = cost
        metrics["cost_formatted"] = format_cost(cost)
        
        # Calculate performance metrics
        perf_metrics = calculate_performance_metrics(transcript_entries, token_totals, debug=debug)
        metrics.update(perf_metrics)
        
        # Calculate performance badge
        if "cache_hit_rate" in metrics and "avg_response_time" in metrics:
            # Badge should be colored unless theme is "none"
            colored = config["theme"] != "none"
            is_powerline = config["style"] == "powerline"
            badge = calculate_performance_badge(
                metrics["cache_hit_rate"],
                metrics["avg_response_time"],
                config["cache_thresholds"],
                config["response_thresholds"],
                colored=colored,
                powerline=is_powerline,
                no_emoji=config["no_emoji"]
            )
            metrics["badge"] = badge
            if debug:
                sys.stderr.write(f"DEBUG: Badge created: {badge[:20]}...\n")
        elif debug:
            has_cache = "cache_hit_rate" in metrics
            has_response = "avg_response_time" in metrics
            sys.stderr.write(f"DEBUG: Badge not created - cache_hit_rate:{has_cache}, avg_response_time:{has_response}\n")
    
    # Add git info to metrics
    if git_info["branch"]:
        metrics["git_info"] = git_info

    metrics.update(collect_cache_and_usage(input_data, config["fields"], int(time.time())))

    # Format and output (pass metrics for field display)
    output = format_output(config, model_info, input_data, metrics)
    # Only add reset if colors were used (to prevent terminal color bleed)
    if config["theme"] != "none":
        print(output + RESET)
    else:
        print(output)
    
    return 0

if __name__ == "__main__":
    sys.exit(main())