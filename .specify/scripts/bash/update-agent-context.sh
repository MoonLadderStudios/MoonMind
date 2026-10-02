#!/usr/bin/env bash

# Update agent context files with information from plan.md
#
# This script maintains AI agent context files by parsing feature specifications 
# and updating agent-specific configuration files with project information.
#
# Agent context files are lasting repository guidance. Routine planning keeps
# feature technology and status in plan.md, so this script changes nothing
# unless --write records an explicit request for a lasting guidance edit.
#
# MAIN FUNCTIONS:
# 1. Environment Validation
#    - Verifies git repository structure and branch information
#    - Checks for required plan.md files and templates
#    - Validates file permissions and accessibility
#
# 2. Plan Data Extraction
#    - Parses plan.md files to extract project metadata
#    - Identifies language/version, frameworks, databases, and project types
#    - Handles missing or incomplete specification data gracefully
#
# 3. Agent File Management
#    - Creates new agent context files from templates when needed
#    - Updates existing agent files with new project information
#    - Preserves manual additions and custom configurations
#    - Writes through symlinked agent files instead of replacing them
#    - Supports multiple AI agent formats and directory structures
#
# 4. Content Generation
#    - Generates language-specific build/test commands
#    - Creates appropriate project directory structures
#    - Updates technology stacks and recent changes sections
#    - Maintains consistent formatting and timestamps
#
# 5. Multi-Agent Support
#    - Handles agent-specific file paths and naming conventions
#    - Supports: Claude, Gemini, Copilot, Cursor, Qwen, opencode, Codex, Windsurf, Kilo Code, Auggie CLI, Roo Code, CodeBuddy CLI, Amp, SHAI, or Amazon Q Developer CLI
#    - Can update single agents or all existing agent files
#    - Creates default Claude file if no agent files exist
#
# Usage: ./update-agent-context.sh --write [agent_type]
# Agent types: claude|gemini|copilot|cursor-agent|qwen|opencode|codex|windsurf|kilocode|auggie|shai|q|bob
# Leave agent_type empty to update all existing agent files

set -e

# Enable strict error handling
set -u
set -o pipefail

#==============================================================================
# Configuration and Global Variables
#==============================================================================

# Get script directory and load common functions
SCRIPT_DIR="$(CDPATH="" cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

# Get all paths and variables from common functions
eval "$(get_feature_paths)"

NEW_PLAN="$IMPL_PLAN"  # Alias for compatibility with existing code
WRITE_AUTHORIZED=false
AGENT_TYPE=""
for arg in "$@"; do
    case "$arg" in
        --write)
            WRITE_AUTHORIZED=true
            ;;
        *)
            if [[ -n "$AGENT_TYPE" ]]; then
                echo "ERROR: Expected at most one agent type, got '$AGENT_TYPE' and '$arg'" >&2
                exit 1
            fi
            AGENT_TYPE="$arg"
            ;;
    esac
done

# Agent-specific file paths  
CLAUDE_FILE="$REPO_ROOT/CLAUDE.md"
GEMINI_FILE="$REPO_ROOT/GEMINI.md"
COPILOT_FILE="$REPO_ROOT/.github/agents/copilot-instructions.md"
CURSOR_FILE="$REPO_ROOT/.cursor/rules/specify-rules.mdc"
QWEN_FILE="$REPO_ROOT/QWEN.md"
AGENTS_FILE="$REPO_ROOT/AGENTS.md"
WINDSURF_FILE="$REPO_ROOT/.windsurf/rules/specify-rules.md"
KILOCODE_FILE="$REPO_ROOT/.kilocode/rules/specify-rules.md"
AUGGIE_FILE="$REPO_ROOT/.augment/rules/specify-rules.md"
ROO_FILE="$REPO_ROOT/.roo/rules/specify-rules.md"
CODEBUDDY_FILE="$REPO_ROOT/CODEBUDDY.md"
AMP_FILE="$REPO_ROOT/AGENTS.md"
SHAI_FILE="$REPO_ROOT/SHAI.md"
Q_FILE="$REPO_ROOT/AGENTS.md"
BOB_FILE="$REPO_ROOT/AGENTS.md"

# Template file
TEMPLATE_FILE="$REPO_ROOT/.specify/templates/agent-file-template.md"

# Resolved agent files already updated in this run
UPDATED_AGENT_FILES=""
AGENT_UPDATE_TEMP_FILE=""

# Global variables for parsed plan data
NEW_LANG=""
NEW_FRAMEWORK=""
NEW_DB=""
NEW_PROJECT_TYPE=""

#==============================================================================
# Utility Functions
#==============================================================================

log_info() {
    echo "INFO: $1"
}

log_success() {
    echo "✓ $1"
}

log_error() {
    echo "ERROR: $1" >&2
}

log_warning() {
    echo "WARNING: $1" >&2
}

# Cleanup function for temporary files
cleanup() {
    local exit_code=$?
    if [[ -n "$AGENT_UPDATE_TEMP_FILE" ]]; then
        rm -f -- "$AGENT_UPDATE_TEMP_FILE" "$AGENT_UPDATE_TEMP_FILE.bak" \
            "$AGENT_UPDATE_TEMP_FILE.bak2" "$AGENT_UPDATE_TEMP_FILE.bak3" \
            "$AGENT_UPDATE_TEMP_FILE.bak4"
    fi
    rm -f /tmp/agent_update_*_$$
    rm -f /tmp/manual_additions_$$
    exit $exit_code
}

# Set up cleanup trap
trap cleanup EXIT INT TERM

#==============================================================================
# Validation Functions
#==============================================================================

validate_environment() {
    # Check if we have a current branch/feature (git or non-git)
    if [[ -z "$CURRENT_BRANCH" ]]; then
        log_error "Unable to determine current feature"
        if [[ "$HAS_GIT" == "true" ]]; then
            log_info "Make sure you're on a feature branch"
        else
            log_info "Set SPECIFY_FEATURE environment variable or create a feature first"
        fi
        exit 1
    fi
    
    # Check if plan.md exists
    if [[ ! -f "$NEW_PLAN" ]]; then
        log_error "No plan.md found at $NEW_PLAN"
        log_info "Make sure you're working on a feature with a corresponding spec directory"
        if [[ "$HAS_GIT" != "true" ]]; then
            log_info "Use: export SPECIFY_FEATURE=your-feature-name or create a new feature first"
        fi
        exit 1
    fi
    
    # Check if template exists (needed for new files)
    if [[ ! -f "$TEMPLATE_FILE" ]]; then
        log_warning "Template file not found at $TEMPLATE_FILE"
        log_warning "Creating new agent files will fail"
    fi
}

#==============================================================================
# Plan Parsing Functions
#==============================================================================

extract_plan_field() {
    local field_pattern="$1"
    local plan_file="$2"
    
    grep "^\*\*${field_pattern}\*\*: " "$plan_file" 2>/dev/null | \
        head -1 | \
        sed "s|^\*\*${field_pattern}\*\*: ||" | \
        sed 's/^[ \t]*//;s/[ \t]*$//' | \
        grep -v "NEEDS CLARIFICATION" | \
        grep -v "^N/A$" || echo ""
}

parse_plan_data() {
    local plan_file="$1"
    
    if [[ ! -f "$plan_file" ]]; then
        log_error "Plan file not found: $plan_file"
        return 1
    fi
    
    if [[ ! -r "$plan_file" ]]; then
        log_error "Plan file is not readable: $plan_file"
        return 1
    fi
    
    log_info "Parsing plan data from $plan_file"
    
    NEW_LANG=$(extract_plan_field "Language/Version" "$plan_file")
    NEW_FRAMEWORK=$(extract_plan_field "Primary Dependencies" "$plan_file")
    NEW_DB=$(extract_plan_field "Storage" "$plan_file")
    NEW_PROJECT_TYPE=$(extract_plan_field "Project Type" "$plan_file")
    
    # Log what we found
    if [[ -n "$NEW_LANG" ]]; then
        log_info "Found language: $NEW_LANG"
    else
        log_warning "No language information found in plan"
    fi
    
    if [[ -n "$NEW_FRAMEWORK" ]]; then
        log_info "Found framework: $NEW_FRAMEWORK"
    fi
    
    if [[ -n "$NEW_DB" ]] && [[ "$NEW_DB" != "N/A" ]]; then
        log_info "Found database: $NEW_DB"
    fi
    
    if [[ -n "$NEW_PROJECT_TYPE" ]]; then
        log_info "Found project type: $NEW_PROJECT_TYPE"
    fi
}

format_technology_stack() {
    local lang="$1"
    local framework="$2"
    local parts=()
    
    # Add non-empty parts
    [[ -n "$lang" && "$lang" != "NEEDS CLARIFICATION" ]] && parts+=("$lang")
    [[ -n "$framework" && "$framework" != "NEEDS CLARIFICATION" && "$framework" != "N/A" ]] && parts+=("$framework")
    
    # Join with proper formatting
    if [[ ${#parts[@]} -eq 0 ]]; then
        echo ""
    elif [[ ${#parts[@]} -eq 1 ]]; then
        echo "${parts[0]}"
    else
        # Join multiple parts with " + "
        local result="${parts[0]}"
        for ((i=1; i<${#parts[@]}; i++)); do
            result="$result + ${parts[i]}"
        done
        echo "$result"
    fi
}

#==============================================================================
# Template and Content Generation Functions
#==============================================================================

get_project_structure() {
    local project_type="$1"
    
    if [[ "$project_type" == *"web"* ]]; then
        echo "backend/\\nfrontend/\\ntests/"
    else
        echo "src/\\ntests/"
    fi
}

get_commands_for_language() {
    local lang="$1"
    
    case "$lang" in
        *"Python"*)
            echo "cd src && pytest && ruff check ."
            ;;
        *"Rust"*)
            echo "cargo test && cargo clippy"
            ;;
        *"JavaScript"*|*"TypeScript"*)
            echo "npm test \\&\\& npm run lint"
            ;;
        *)
            echo "# Add commands for $lang"
            ;;
    esac
}

get_language_conventions() {
    local lang="$1"
    echo "$lang: Follow standard conventions"
}

resolve_agent_file() {
    local path="$1"
    local link
    local symlink_hops=0

    while [[ -L "$path" ]]; do
        ((symlink_hops += 1))
        if ((symlink_hops > 40)); then
            log_error "Agent-file symlink cycle or chain deeper than 40 links: $1"
            return 1
        fi
        if ! link=$(readlink "$path"); then
            log_error "Failed to read agent-file symlink: $path"
            return 1
        fi
        if [[ "$link" == /* ]]; then
            path="$link"
        else
            path="$(dirname "$path")/$link"
        fi
    done

    if [[ -d "$(dirname "$path")" ]]; then
        path="$(CDPATH="" cd "$(dirname "$path")" && pwd -P)/$(basename "$path")"
    fi
    echo "$path"
}

is_manual_additions_start() {
    [[ "$1" == "<!-- MANUAL ADDITIONS START -->" ]]
}

agent_file_accepts_plan_history() {
    local target_file="$1"
    local logical_target_file="${2:-$target_file}"
    [[ "$(basename "$logical_target_file")" != "AGENTS.md" ]] && \
        [[ "$(basename "$target_file")" != "AGENTS.md" ]]
}

create_new_agent_file() {
    local target_file="$1"
    local temp_file="$2"
    local project_name="$3"
    local current_date="$4"
    local logical_target_file="${5:-$target_file}"
    
    if [[ ! -f "$TEMPLATE_FILE" ]]; then
        log_error "Template not found at $TEMPLATE_FILE"
        return 1
    fi
    
    if [[ ! -r "$TEMPLATE_FILE" ]]; then
        log_error "Template file is not readable: $TEMPLATE_FILE"
        return 1
    fi
    
    log_info "Creating new agent context file from template..."
    
    # Keep the temporary file writable during generation, without template modes.
    if ! cp "$TEMPLATE_FILE" "$temp_file"; then
        log_error "Failed to copy template file"
        return 1
    fi
    
    # Replace template placeholders
    local project_structure
    project_structure=$(get_project_structure "$NEW_PROJECT_TYPE")
    
    local commands
    commands=$(get_commands_for_language "$NEW_LANG")
    
    local language_conventions
    language_conventions=$(get_language_conventions "$NEW_LANG")
    
    # Perform substitutions with error checking using safer approach
    # Escape special characters for sed by using a different delimiter or escaping
    local escaped_lang=$(printf '%s\n' "$NEW_LANG" | sed 's/[\[\.*^$()+{}|]/\\&/g')
    local escaped_framework=$(printf '%s\n' "$NEW_FRAMEWORK" | sed 's/[\[\.*^$()+{}|]/\\&/g')
    local escaped_branch=$(printf '%s\n' "$CURRENT_BRANCH" | sed 's/[\[\.*^$()+{}|]/\\&/g')
    
    # Build technology stack and recent change strings conditionally
    local tech_stack
    if [[ -n "$escaped_lang" && -n "$escaped_framework" ]]; then
        tech_stack="- $escaped_lang + $escaped_framework ($escaped_branch)"
    elif [[ -n "$escaped_lang" ]]; then
        tech_stack="- $escaped_lang ($escaped_branch)"
    elif [[ -n "$escaped_framework" ]]; then
        tech_stack="- $escaped_framework ($escaped_branch)"
    else
        tech_stack="- ($escaped_branch)"
    fi

    local recent_change
    if [[ -n "$escaped_lang" && -n "$escaped_framework" ]]; then
        recent_change="- $escaped_branch: Added $escaped_lang + $escaped_framework"
    elif [[ -n "$escaped_lang" ]]; then
        recent_change="- $escaped_branch: Added $escaped_lang"
    elif [[ -n "$escaped_framework" ]]; then
        recent_change="- $escaped_branch: Added $escaped_framework"
    else
        recent_change="- $escaped_branch: Added"
    fi

    local substitutions=(
        "s|\[PROJECT NAME\]|$project_name|"
        "s|\[DATE\]|$current_date|"
        "s|\[EXTRACTED FROM ALL PLAN.MD FILES\]|$tech_stack|"
        "s|\[ACTUAL STRUCTURE FROM PLANS\]|$project_structure|g"
        "s|\[ONLY COMMANDS FOR ACTIVE TECHNOLOGIES\]|$commands|"
        "s|\[LANGUAGE-SPECIFIC, ONLY FOR LANGUAGES IN USE\]|$language_conventions|"
        "s|\[LAST 3 FEATURES AND WHAT THEY ADDED\]|$recent_change|"
    )
    
    for substitution in "${substitutions[@]}"; do
        if ! sed -i.bak -e "$substitution" "$temp_file"; then
            log_error "Failed to perform substitution: $substitution"
            rm -f "$temp_file" "$temp_file.bak"
            return 1
        fi
    done
    
    # Convert \n sequences to actual newlines
    newline=$(printf '\n')
    sed -i.bak2 "s/\\\\n/${newline}/g" "$temp_file" || return 1

    if ! agent_file_accepts_plan_history "$target_file" "$logical_target_file"; then
        sed -i.bak3 '/^## Active Technologies$/,/^## Project Structure$/{
            /^## Project Structure$/!d
        }' "$temp_file" || return 1
        sed -i.bak4 '/^## Recent Changes$/,/^<!-- MANUAL ADDITIONS START -->$/{
            /^<!-- MANUAL ADDITIONS START -->$/!d
        }' "$temp_file" || return 1
    fi
    
    # Clean up backup files
    rm -f "$temp_file.bak" "$temp_file.bak2" "$temp_file.bak3" "$temp_file.bak4"

    # Publish with the same permissions as a normal file created by this caller.
    local file_mode
    printf -v file_mode '%03o' "$((0666 & ~$(umask)))" || return 1
    if ! chmod "$file_mode" "$temp_file"; then
        log_error "Failed to apply consumer file-creation permissions"
        return 1
    fi
    
    return 0
}




update_existing_agent_file() {
    local target_file="$1"
    local current_date="$2"
    local logical_target_file="${3:-$target_file}"
    
    log_info "Updating existing agent context file..."
    
    # Use a single temporary file for atomic update
    local temp_file
    temp_file=$(mktemp "$(dirname "$target_file")/.agent_update.XXXXXX") || {
        log_error "Failed to create temporary file"
        return 1
    }
    AGENT_UPDATE_TEMP_FILE="$temp_file"
    if ! cp -p "$target_file" "$temp_file" || ! : > "$temp_file"; then
        log_error "Failed to prepare target metadata for atomic update"
        rm -f -- "$temp_file"
        AGENT_UPDATE_TEMP_FILE=""
        return 1
    fi
    
    # Process the file in one pass
    local tech_stack
    tech_stack=$(format_technology_stack "$NEW_LANG" "$NEW_FRAMEWORK")
    local new_tech_entries=()
    local new_change_entry=""
    local accepts_plan_history=false
    if agent_file_accepts_plan_history "$target_file" "$logical_target_file"; then
        accepts_plan_history=true
    fi
    
    # Prepare new technology entries
    if [[ $accepts_plan_history == true ]] && [[ -n "$tech_stack" ]] && ! grep -q "$tech_stack" "$target_file"; then
        new_tech_entries+=("- $tech_stack ($CURRENT_BRANCH)")
    fi
    
    if [[ $accepts_plan_history == true ]] && [[ -n "$NEW_DB" ]] && [[ "$NEW_DB" != "N/A" ]] && [[ "$NEW_DB" != "NEEDS CLARIFICATION" ]] && ! grep -q "$NEW_DB" "$target_file"; then
        new_tech_entries+=("- $NEW_DB ($CURRENT_BRANCH)")
    fi
    
    # Prepare new change entry
    if [[ $accepts_plan_history == true ]] && [[ -n "$tech_stack" ]]; then
        new_change_entry="- $CURRENT_BRANCH: Added $tech_stack"
    elif [[ $accepts_plan_history == true ]] && [[ -n "$NEW_DB" ]] && [[ "$NEW_DB" != "N/A" ]] && [[ "$NEW_DB" != "NEEDS CLARIFICATION" ]]; then
        new_change_entry="- $CURRENT_BRANCH: Added $NEW_DB"
    fi
    
    if [[ ${#new_tech_entries[@]} -eq 0 ]] && [[ -z "$new_change_entry" ]]; then
        log_info "No new plan context for $target_file"
        rm -f "$temp_file"
        AGENT_UPDATE_TEMP_FILE=""
        return 0
    fi

    # Check if sections exist in the file
    local has_active_technologies=0
    local has_recent_changes=0
    
    if grep -q "^## Active Technologies" "$target_file" 2>/dev/null; then
        has_active_technologies=1
    fi
    
    if grep -q "^## Recent Changes" "$target_file" 2>/dev/null; then
        has_recent_changes=1
    fi
    
    # Process file line by line
    local in_tech_section=false
    local in_changes_section=false
    local tech_entries_added=false
    local changes_entries_added=false
    local existing_changes_count=0
    local file_ended=false
    
    while IFS= read -r line || [[ -n "$line" ]]; do
        # Handle Active Technologies section
        if [[ "$line" == "## Active Technologies" ]]; then
            echo "$line" >> "$temp_file" || return 1
            in_tech_section=true
            in_changes_section=false
            continue
        elif [[ $in_tech_section == true ]] && { [[ "$line" =~ ^##[[:space:]] ]] || is_manual_additions_start "$line"; }; then
            # Add new tech entries before closing the section, then let the
            # following handlers process the line that closed it
            if [[ $tech_entries_added == false ]] && [[ ${#new_tech_entries[@]} -gt 0 ]]; then
                printf '%s\n' "${new_tech_entries[@]}" >> "$temp_file" || return 1
                tech_entries_added=true
            fi
            in_tech_section=false
        elif [[ $in_tech_section == true ]] && [[ -z "$line" ]]; then
            # Add new tech entries before empty line in tech section
            if [[ $tech_entries_added == false ]] && [[ ${#new_tech_entries[@]} -gt 0 ]]; then
                printf '%s\n' "${new_tech_entries[@]}" >> "$temp_file" || return 1
                tech_entries_added=true
            fi
            echo "$line" >> "$temp_file" || return 1
            continue
        fi
        
        # Handle Recent Changes section
        if [[ "$line" == "## Recent Changes" ]]; then
            echo "$line" >> "$temp_file" || return 1
            # Add new change entry right after the heading
            if [[ -n "$new_change_entry" ]]; then
                echo "$new_change_entry" >> "$temp_file" || return 1
            fi
            in_changes_section=true
            changes_entries_added=true
            continue
        elif [[ $in_changes_section == true ]] && { [[ "$line" =~ ^##[[:space:]] ]] || is_manual_additions_start "$line"; }; then
            echo "$line" >> "$temp_file" || return 1
            in_changes_section=false
            continue
        elif [[ $in_changes_section == true ]] && [[ "$line" == "- "* ]]; then
            # Keep only first 2 existing changes
            if [[ $existing_changes_count -lt 2 ]]; then
                echo "$line" >> "$temp_file" || return 1
                ((existing_changes_count++))
            fi
            continue
        fi
        
        # Update timestamp
        if [[ "$line" =~ \*\*Last\ updated\*\*:.*[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9] ]]; then
            echo "$line" | sed "s/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]/$current_date/" >> "$temp_file" || return 1
        else
            echo "$line" >> "$temp_file" || return 1
        fi
    done < "$target_file"
    
    # Post-loop check: if we're still in the Active Technologies section and haven't added new entries
    if [[ $in_tech_section == true ]] && [[ $tech_entries_added == false ]] && [[ ${#new_tech_entries[@]} -gt 0 ]]; then
        printf '%s\n' "${new_tech_entries[@]}" >> "$temp_file" || return 1
        tech_entries_added=true
    fi
    
    # If sections don't exist, add them at the end of the file
    if [[ $has_active_technologies -eq 0 ]] && [[ ${#new_tech_entries[@]} -gt 0 ]]; then
        echo "" >> "$temp_file" || return 1
        echo "## Active Technologies" >> "$temp_file" || return 1
        printf '%s\n' "${new_tech_entries[@]}" >> "$temp_file" || return 1
        tech_entries_added=true
    fi
    
    if [[ $has_recent_changes -eq 0 ]] && [[ -n "$new_change_entry" ]]; then
        echo "" >> "$temp_file" || return 1
        echo "## Recent Changes" >> "$temp_file" || return 1
        echo "$new_change_entry" >> "$temp_file" || return 1
        changes_entries_added=true
    fi
    
    # cp -p can succeed without preserving a foreign uid/gid for this account.
    local target_ownership temp_ownership
    if ! target_ownership=$(LC_ALL=C ls -nd "$target_file" | awk 'NR == 1 { print $3 ":" $4 }') || \
       ! temp_ownership=$(LC_ALL=C ls -nd "$temp_file" | awk 'NR == 1 { print $3 ":" $4 }'); then
        log_error "Failed to inspect guidance ownership before atomic replacement"
        return 1
    fi
    if [[ ! "$target_ownership" =~ ^[0-9]+:[0-9]+$ ]] || [[ "$target_ownership" != "$temp_ownership" ]]; then
        log_error "Cannot preserve existing guidance ownership ($target_ownership): replacement has $temp_ownership"
        return 1
    fi

    # Replace only after generation and metadata checks succeed.
    if ! mv -f "$temp_file" "$target_file"; then
        log_error "Failed to update target file"
        rm -f "$temp_file"
        return 1
    fi
    rm -f "$temp_file"
    AGENT_UPDATE_TEMP_FILE=""
    
    return 0
}
#==============================================================================
# Main Agent File Update Function
#==============================================================================

update_agent_file() {
    local target_file="$1"
    local agent_name="$2"
    local logical_target_file="$target_file"
    
    if [[ -z "$target_file" ]] || [[ -z "$agent_name" ]]; then
        log_error "update_agent_file requires target_file and agent_name parameters"
        return 1
    fi
    
    target_file=$(resolve_agent_file "$target_file") || return 1
    if [[ -e "$target_file" && ! -f "$target_file" ]]; then
        log_error "Unsupported non-regular agent context target: $target_file"
        return 1
    fi
    if [[ "$UPDATED_AGENT_FILES" == *$'\n'"$target_file"$'\n'* ]]; then
        log_info "Skipping $agent_name context file already updated: $target_file"
        return 0
    fi
    UPDATED_AGENT_FILES="${UPDATED_AGENT_FILES:-$'\n'}$target_file"$'\n'

    log_info "Updating $agent_name context file: $target_file"
    
    local project_name
    project_name=$(basename "$REPO_ROOT")
    local current_date
    current_date=$(date +%Y-%m-%d)
    
    # Create directory if it doesn't exist
    local target_dir
    target_dir=$(dirname "$target_file")
    if [[ ! -d "$target_dir" ]]; then
        if ! mkdir -p "$target_dir"; then
            log_error "Failed to create directory: $target_dir"
            return 1
        fi
    fi
    
    if [[ ! -f "$target_file" ]]; then
        # Create new file from template
        local temp_file
        temp_file=$(mktemp "$target_dir/.agent_update.XXXXXX") || {
            log_error "Failed to create temporary file"
            return 1
        }
        AGENT_UPDATE_TEMP_FILE="$temp_file"
        
        if create_new_agent_file "$target_file" "$temp_file" "$project_name" "$current_date" "$logical_target_file"; then
            if mv -f "$temp_file" "$target_file"; then
                rm -f "$temp_file"
                AGENT_UPDATE_TEMP_FILE=""
                log_success "Created new $agent_name context file"
            else
                log_error "Failed to move temporary file to $target_file"
                rm -f "$temp_file"
                return 1
            fi
        else
            log_error "Failed to create new agent file"
            rm -f "$temp_file"
            AGENT_UPDATE_TEMP_FILE=""
            return 1
        fi
    else
        # Update existing file
        if [[ ! -r "$target_file" ]]; then
            log_error "Cannot read existing file: $target_file"
            return 1
        fi
        
        if [[ ! -w "$target_file" ]]; then
            log_error "Cannot write to existing file: $target_file"
            return 1
        fi
        
        if update_existing_agent_file "$target_file" "$current_date" "$logical_target_file"; then
            log_success "Updated existing $agent_name context file"
        else
            log_error "Failed to update existing agent file"
            rm -f -- "$AGENT_UPDATE_TEMP_FILE"
            AGENT_UPDATE_TEMP_FILE=""
            return 1
        fi
    fi
    
    return 0
}

#==============================================================================
# Agent Selection and Processing
#==============================================================================

update_specific_agent() {
    local agent_type="$1"
    
    case "$agent_type" in
        claude)
            update_agent_file "$CLAUDE_FILE" "Claude Code"
            ;;
        gemini)
            update_agent_file "$GEMINI_FILE" "Gemini CLI"
            ;;
        copilot)
            update_agent_file "$COPILOT_FILE" "GitHub Copilot"
            ;;
        cursor-agent)
            update_agent_file "$CURSOR_FILE" "Cursor IDE"
            ;;
        qwen)
            update_agent_file "$QWEN_FILE" "Qwen Code"
            ;;
        opencode)
            update_agent_file "$AGENTS_FILE" "opencode"
            ;;
        codex)
            update_agent_file "$AGENTS_FILE" "Codex CLI"
            ;;
        windsurf)
            update_agent_file "$WINDSURF_FILE" "Windsurf"
            ;;
        kilocode)
            update_agent_file "$KILOCODE_FILE" "Kilo Code"
            ;;
        auggie)
            update_agent_file "$AUGGIE_FILE" "Auggie CLI"
            ;;
        roo)
            update_agent_file "$ROO_FILE" "Roo Code"
            ;;
        codebuddy)
            update_agent_file "$CODEBUDDY_FILE" "CodeBuddy CLI"
            ;;
        amp)
            update_agent_file "$AMP_FILE" "Amp"
            ;;
        shai)
            update_agent_file "$SHAI_FILE" "SHAI"
            ;;
        q)
            update_agent_file "$Q_FILE" "Amazon Q Developer CLI"
            ;;
        bob)
            update_agent_file "$BOB_FILE" "IBM Bob"
            ;;
        *)
            log_error "Unknown agent type '$agent_type'"
            log_error "Expected: claude|gemini|copilot|cursor-agent|qwen|opencode|codex|windsurf|kilocode|auggie|roo|amp|shai|q|bob"
            exit 1
            ;;
    esac
}

update_all_existing_agents() {
    local found_agent=false

    # Update logical AGENTS first so shared aliases retain its protected identity.
    if [[ -e "$AGENTS_FILE" || -L "$AGENTS_FILE" ]]; then
        update_agent_file "$AGENTS_FILE" "Codex/opencode" || return 1
        found_agent=true
    fi
    
    # Check each possible agent file and update if it exists
    if [[ -e "$CLAUDE_FILE" || -L "$CLAUDE_FILE" ]]; then
        update_agent_file "$CLAUDE_FILE" "Claude Code" || return 1
        found_agent=true
    fi
    
    if [[ -e "$GEMINI_FILE" || -L "$GEMINI_FILE" ]]; then
        update_agent_file "$GEMINI_FILE" "Gemini CLI" || return 1
        found_agent=true
    fi
    
    if [[ -e "$COPILOT_FILE" || -L "$COPILOT_FILE" ]]; then
        update_agent_file "$COPILOT_FILE" "GitHub Copilot" || return 1
        found_agent=true
    fi
    
    if [[ -e "$CURSOR_FILE" || -L "$CURSOR_FILE" ]]; then
        update_agent_file "$CURSOR_FILE" "Cursor IDE" || return 1
        found_agent=true
    fi
    
    if [[ -e "$QWEN_FILE" || -L "$QWEN_FILE" ]]; then
        update_agent_file "$QWEN_FILE" "Qwen Code" || return 1
        found_agent=true
    fi
    
    if [[ -e "$WINDSURF_FILE" || -L "$WINDSURF_FILE" ]]; then
        update_agent_file "$WINDSURF_FILE" "Windsurf" || return 1
        found_agent=true
    fi
    
    if [[ -e "$KILOCODE_FILE" || -L "$KILOCODE_FILE" ]]; then
        update_agent_file "$KILOCODE_FILE" "Kilo Code" || return 1
        found_agent=true
    fi

    if [[ -e "$AUGGIE_FILE" || -L "$AUGGIE_FILE" ]]; then
        update_agent_file "$AUGGIE_FILE" "Auggie CLI" || return 1
        found_agent=true
    fi
    
    if [[ -e "$ROO_FILE" || -L "$ROO_FILE" ]]; then
        update_agent_file "$ROO_FILE" "Roo Code" || return 1
        found_agent=true
    fi

    if [[ -e "$CODEBUDDY_FILE" || -L "$CODEBUDDY_FILE" ]]; then
        update_agent_file "$CODEBUDDY_FILE" "CodeBuddy CLI" || return 1
        found_agent=true
    fi

    if [[ -e "$SHAI_FILE" || -L "$SHAI_FILE" ]]; then
        update_agent_file "$SHAI_FILE" "SHAI" || return 1
        found_agent=true
    fi

    if [[ -e "$Q_FILE" || -L "$Q_FILE" ]]; then
        update_agent_file "$Q_FILE" "Amazon Q Developer CLI" || return 1
        found_agent=true
    fi
    
    if [[ -e "$BOB_FILE" || -L "$BOB_FILE" ]]; then
        update_agent_file "$BOB_FILE" "IBM Bob" || return 1
        found_agent=true
    fi
    
    # If no agent files exist, create a default Claude file
    if [[ "$found_agent" == false ]]; then
        log_info "No existing agent files found, creating default Claude file..."
        update_agent_file "$CLAUDE_FILE" "Claude Code" || return 1
    fi
}
print_summary() {
    echo
    log_info "Summary of changes:"
    
    if [[ -n "$NEW_LANG" ]]; then
        echo "  - Added language: $NEW_LANG"
    fi
    
    if [[ -n "$NEW_FRAMEWORK" ]]; then
        echo "  - Added framework: $NEW_FRAMEWORK"
    fi
    
    if [[ -n "$NEW_DB" ]] && [[ "$NEW_DB" != "N/A" ]]; then
        echo "  - Added database: $NEW_DB"
    fi
    
    echo

    log_info "Usage: $0 --write [claude|gemini|copilot|cursor-agent|qwen|opencode|codex|windsurf|kilocode|auggie|codebuddy|shai|q|bob]"
}

#==============================================================================
# Main Execution
#==============================================================================

main() {
    if [[ "$WRITE_AUTHORIZED" != true ]]; then
        log_info "No agent context files changed; feature technology and status stay in plan.md."
        log_info "Pass --write only when the user explicitly asked to record this plan in lasting agent guidance."
        exit 0
    fi

    # Validate environment before proceeding
    validate_environment
    
    log_info "=== Updating agent context files for feature $CURRENT_BRANCH ==="
    
    # Parse the plan file to extract project information
    if ! parse_plan_data "$NEW_PLAN"; then
        log_error "Failed to parse plan data"
        exit 1
    fi
    
    # Process based on agent type argument
    local success=true
    
    if [[ -z "$AGENT_TYPE" ]]; then
        # No specific agent provided - update all existing agent files
        log_info "No agent specified, updating all existing agent files..."
        if ! update_all_existing_agents; then
            success=false
        fi
    else
        # Specific agent provided - update only that agent
        log_info "Updating specific agent: $AGENT_TYPE"
        if ! update_specific_agent "$AGENT_TYPE"; then
            success=false
        fi
    fi
    
    # Print summary
    print_summary
    
    if [[ "$success" == true ]]; then
        log_success "Agent context update completed successfully"
        exit 0
    else
        log_error "Agent context update completed with errors"
        exit 1
    fi
}

# Execute main function if script is run directly
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
