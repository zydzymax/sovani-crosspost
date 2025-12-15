#!/bin/bash
# FFmpeg Profiles for SoVAni Crosspost v2.1
# Aspect ratio conversion functions for different social media platforms

set -euo pipefail

# Colors for output
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly NC='\033[0m' # No Color

# Logging function
log() {
    local level="$1"
    shift
    case "$level" in
        "ERROR") echo -e "${RED}[ERROR]${NC} $*" >&2 ;;
        "WARN")  echo -e "${YELLOW}[WARN]${NC} $*" >&2 ;;
        "INFO")  echo -e "${GREEN}[INFO]${NC} $*" >&2 ;;
        *)       echo "$*" >&2 ;;
    esac
}

# Validate input file
validate_input() {
    local input_file="$1"
    
    if [[ ! -f "$input_file" ]]; then
        log "ERROR" "Input file does not exist: $input_file"
        return 1
    fi
    
    if [[ ! -r "$input_file" ]]; then
        log "ERROR" "Input file is not readable: $input_file"
        return 1
    fi
    
    # Check if file is a valid media file
    if ! ffprobe -v quiet -select_streams v:0 -show_entries stream=width,height -of csv=p=0 "$input_file" >/dev/null 2>&1; then
        log "ERROR" "Input file is not a valid video/image: $input_file"
        return 1
    fi
    
    return 0
}

# Get video dimensions
get_dimensions() {
    local input_file="$1"
    
    if ! validate_input "$input_file"; then
        return 1
    fi
    
    ffprobe -v quiet -select_streams v:0 -show_entries stream=width,height -of csv=p=0 "$input_file" 2>/dev/null
}

# Calculate padding for aspect ratio conversion
calculate_padding() {
    local current_width="$1"
    local current_height="$2"
    local target_width="$3" 
    local target_height="$4"
    
    # Calculate current and target ratios
    local current_ratio=$(echo "scale=6; $current_width / $current_height" | bc -l)
    local target_ratio=$(echo "scale=6; $target_width / $target_height" | bc -l)
    
    # Determine if we need vertical or horizontal padding
    if (( $(echo "$current_ratio > $target_ratio" | bc -l) )); then
        # Current is wider - need vertical padding
        local new_height=$(echo "scale=0; $current_width / $target_ratio" | bc)
        local pad_top=$(echo "scale=0; ($new_height - $current_height) / 2" | bc)
        echo "0:$pad_top:0:$pad_top"
    else
        # Current is taller - need horizontal padding
        local new_width=$(echo "scale=0; $current_height * $target_ratio" | bc)
        local pad_left=$(echo "scale=0; ($new_width - $current_width) / 2" | bc)
        echo "$pad_left:0:$pad_left:0"
    fi
}

# Common FFmpeg options
get_common_options() {
    echo "-c:v libx264 -preset medium -crf 23 -c:a aac -b:a 128k -movflags +faststart"
}

# Convert to 9:16 aspect ratio (Instagram Stories, TikTok, YouTube Shorts)
to_9x16() {
    local input_file="$1"
    local output_file="$2"
    local strategy="${3:-pad}"  # pad, crop, stretch
    local background_color="${4:-black}"
    
    log "INFO" "Converting to 9:16 aspect ratio: $input_file -> $output_file"
    
    if ! validate_input "$input_file"; then
        return 1
    fi
    
    local dimensions
    if ! dimensions=$(get_dimensions "$input_file"); then
        log "ERROR" "Failed to get video dimensions"
        return 1
    fi
    
    local width height
    IFS=',' read -r width height <<< "$dimensions"
    
    log "INFO" "Input dimensions: ${width}x${height}"
    
    case "$strategy" in
        "pad")
            # Add padding to maintain aspect ratio
            local filter="scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=$background_color"
            ;;
        "crop")
            # Crop to fit aspect ratio
            local filter="scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
            ;;
        "stretch")
            # Stretch to fit (may distort)
            local filter="scale=1080:1920"
            ;;
        *)
            log "ERROR" "Unknown strategy: $strategy. Use: pad, crop, or stretch"
            return 1
            ;;
    esac
    
    local common_opts
    common_opts=$(get_common_options)
    
    if ffmpeg -i "$input_file" -vf "$filter" $common_opts -y "$output_file" 2>/dev/null; then
        log "INFO" "Successfully converted to 9:16: $output_file"
        return 0
    else
        log "ERROR" "FFmpeg conversion failed"
        return 1
    fi
}

# Convert to 4:5 aspect ratio (Instagram Feed)
to_4x5() {
    local input_file="$1"
    local output_file="$2"
    local strategy="${3:-pad}"
    local background_color="${4:-black}"
    
    log "INFO" "Converting to 4:5 aspect ratio: $input_file -> $output_file"
    
    if ! validate_input "$input_file"; then
        return 1
    fi
    
    local dimensions
    if ! dimensions=$(get_dimensions "$input_file"); then
        log "ERROR" "Failed to get video dimensions"
        return 1
    fi
    
    local width height
    IFS=',' read -r width height <<< "$dimensions"
    
    log "INFO" "Input dimensions: ${width}x${height}"
    
    case "$strategy" in
        "pad")
            local filter="scale=1080:1350:force_original_aspect_ratio=decrease,pad=1080:1350:(ow-iw)/2:(oh-ih)/2:color=$background_color"
            ;;
        "crop")
            local filter="scale=1080:1350:force_original_aspect_ratio=increase,crop=1080:1350"
            ;;
        "stretch")
            local filter="scale=1080:1350"
            ;;
        *)
            log "ERROR" "Unknown strategy: $strategy. Use: pad, crop, or stretch"
            return 1
            ;;
    esac
    
    local common_opts
    common_opts=$(get_common_options)
    
    if ffmpeg -i "$input_file" -vf "$filter" $common_opts -y "$output_file" 2>/dev/null; then
        log "INFO" "Successfully converted to 4:5: $output_file"
        return 0
    else
        log "ERROR" "FFmpeg conversion failed"
        return 1
    fi
}

# Convert to 1:1 aspect ratio (Instagram Square)
to_1x1() {
    local input_file="$1"
    local output_file="$2"
    local strategy="${3:-pad}"
    local background_color="${4:-black}"
    
    log "INFO" "Converting to 1:1 aspect ratio: $input_file -> $output_file"
    
    if ! validate_input "$input_file"; then
        return 1
    fi
    
    local dimensions
    if ! dimensions=$(get_dimensions "$input_file"); then
        log "ERROR" "Failed to get video dimensions"
        return 1
    fi
    
    local width height
    IFS=',' read -r width height <<< "$dimensions"
    
    log "INFO" "Input dimensions: ${width}x${height}"
    
    case "$strategy" in
        "pad")
            local filter="scale=1080:1080:force_original_aspect_ratio=decrease,pad=1080:1080:(ow-iw)/2:(oh-ih)/2:color=$background_color"
            ;;
        "crop")
            local filter="scale=1080:1080:force_original_aspect_ratio=increase,crop=1080:1080"
            ;;
        "stretch")
            local filter="scale=1080:1080"
            ;;
        *)
            log "ERROR" "Unknown strategy: $strategy. Use: pad, crop, or stretch"
            return 1
            ;;
    esac
    
    local common_opts
    common_opts=$(get_common_options)
    
    if ffmpeg -i "$input_file" -vf "$filter" $common_opts -y "$output_file" 2>/dev/null; then
        log "INFO" "Successfully converted to 1:1: $output_file"
        return 0
    else
        log "ERROR" "FFmpeg conversion failed"
        return 1
    fi
}

# Convert to 16:9 aspect ratio (YouTube, VK, Facebook)
to_16x9() {
    local input_file="$1"
    local output_file="$2"
    local strategy="${3:-pad}"
    local background_color="${4:-black}"
    
    log "INFO" "Converting to 16:9 aspect ratio: $input_file -> $output_file"
    
    if ! validate_input "$input_file"; then
        return 1
    fi
    
    local dimensions
    if ! dimensions=$(get_dimensions "$input_file"); then
        log "ERROR" "Failed to get video dimensions"
        return 1
    fi
    
    local width height
    IFS=',' read -r width height <<< "$dimensions"
    
    log "INFO" "Input dimensions: ${width}x${height}"
    
    case "$strategy" in
        "pad")
            local filter="scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=$background_color"
            ;;
        "crop")
            local filter="scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080"
            ;;
        "stretch")
            local filter="scale=1920:1080"
            ;;
        *)
            log "ERROR" "Unknown strategy: $strategy. Use: pad, crop, or stretch"
            return 1
            ;;
    esac
    
    local common_opts
    common_opts=$(get_common_options)
    
    if ffmpeg -i "$input_file" -vf "$filter" $common_opts -y "$output_file" 2>/dev/null; then
        log "INFO" "Successfully converted to 16:9: $output_file"
        return 0
    else
        log "ERROR" "FFmpeg conversion failed"
        return 1
    fi
}

# Convert to custom aspect ratio
to_custom() {
    local input_file="$1"
    local output_file="$2"
    local target_width="$3"
    local target_height="$4"
    local strategy="${5:-pad}"
    local background_color="${6:-black}"
    
    log "INFO" "Converting to ${target_width}:${target_height} aspect ratio: $input_file -> $output_file"
    
    if ! validate_input "$input_file"; then
        return 1
    fi
    
    case "$strategy" in
        "pad")
            local filter="scale=${target_width}:${target_height}:force_original_aspect_ratio=decrease,pad=${target_width}:${target_height}:(ow-iw)/2:(oh-ih)/2:color=$background_color"
            ;;
        "crop")
            local filter="scale=${target_width}:${target_height}:force_original_aspect_ratio=increase,crop=${target_width}:${target_height}"
            ;;
        "stretch")
            local filter="scale=${target_width}:${target_height}"
            ;;
        *)
            log "ERROR" "Unknown strategy: $strategy. Use: pad, crop, or stretch"
            return 1
            ;;
    esac
    
    local common_opts
    common_opts=$(get_common_options)
    
    if ffmpeg -i "$input_file" -vf "$filter" $common_opts -y "$output_file" 2>/dev/null; then
        log "INFO" "Successfully converted to ${target_width}:${target_height}: $output_file"
        return 0
    else
        log "ERROR" "FFmpeg conversion failed"
        return 1
    fi
}

# Get aspect ratio information
get_aspect_info() {
    local input_file="$1"
    
    if ! validate_input "$input_file"; then
        return 1
    fi
    
    local dimensions
    if ! dimensions=$(get_dimensions "$input_file"); then
        log "ERROR" "Failed to get video dimensions"
        return 1
    fi
    
    local width height
    IFS=',' read -r width height <<< "$dimensions"
    
    # Calculate aspect ratio
    local gcd
    gcd=$(gcd_calc "$width" "$height")
    local ratio_w=$((width / gcd))
    local ratio_h=$((height / gcd))
    
    echo "Dimensions: ${width}x${height}"
    echo "Aspect Ratio: ${ratio_w}:${ratio_h}"
    echo "Decimal Ratio: $(echo "scale=4; $width / $height" | bc -l)"
    
    # Identify common aspect ratios
    case "${ratio_w}:${ratio_h}" in
        "16:9")   echo "Format: Landscape (YouTube, VK, Facebook)" ;;
        "9:16")   echo "Format: Portrait (Instagram Stories, TikTok)" ;;
        "4:5")    echo "Format: Instagram Feed" ;;
        "1:1")    echo "Format: Square (Instagram Square)" ;;
        "4:3")    echo "Format: Traditional TV" ;;
        "21:9")   echo "Format: Ultrawide" ;;
        *)        echo "Format: Custom (${ratio_w}:${ratio_h})" ;;
    esac
}

# Helper function to calculate GCD
gcd_calc() {
    local a="$1"
    local b="$2"
    
    while [[ $b -ne 0 ]]; do
        local temp=$b
        b=$((a % b))
        a=$temp
    done
    
    echo "$a"
}

# Quality profiles for different use cases
get_quality_profile() {
    local profile="$1"
    
    case "$profile" in
        "high")
            echo "-c:v libx264 -preset slow -crf 18 -c:a aac -b:a 192k"
            ;;
        "medium")
            echo "-c:v libx264 -preset medium -crf 23 -c:a aac -b:a 128k"
            ;;
        "low")
            echo "-c:v libx264 -preset fast -crf 28 -c:a aac -b:a 96k"
            ;;
        "web")
            echo "-c:v libx264 -preset fast -crf 25 -c:a aac -b:a 128k -movflags +faststart"
            ;;
        *)
            log "ERROR" "Unknown quality profile: $profile. Use: high, medium, low, web"
            return 1
            ;;
    esac
}

# Batch convert function
batch_convert() {
    local input_dir="$1"
    local output_dir="$2"
    local aspect_ratio="$3"
    local strategy="${4:-pad}"
    local quality="${5:-medium}"
    
    if [[ ! -d "$input_dir" ]]; then
        log "ERROR" "Input directory does not exist: $input_dir"
        return 1
    fi
    
    mkdir -p "$output_dir"
    
    local count=0
    local success=0
    local failed=0
    
    for file in "$input_dir"/*.{mp4,avi,mov,mkv,webm,m4v}; do
        [[ -f "$file" ]] || continue
        
        local basename
        basename=$(basename "$file" | sed 's/\.[^.]*$//')
        local output_file="$output_dir/${basename}_${aspect_ratio}.mp4"
        
        log "INFO" "Processing: $file"
        count=$((count + 1))
        
        case "$aspect_ratio" in
            "9x16")  
                if to_9x16 "$file" "$output_file" "$strategy"; then
                    success=$((success + 1))
                else
                    failed=$((failed + 1))
                fi
                ;;
            "4x5")   
                if to_4x5 "$file" "$output_file" "$strategy"; then
                    success=$((success + 1))
                else
                    failed=$((failed + 1))
                fi
                ;;
            "1x1")   
                if to_1x1 "$file" "$output_file" "$strategy"; then
                    success=$((success + 1))
                else
                    failed=$((failed + 1))
                fi
                ;;
            "16x9")  
                if to_16x9 "$file" "$output_file" "$strategy"; then
                    success=$((success + 1))
                else
                    failed=$((failed + 1))
                fi
                ;;
            *)
                log "ERROR" "Unknown aspect ratio: $aspect_ratio"
                failed=$((failed + 1))
                ;;
        esac
    done
    
    log "INFO" "Batch conversion completed: $success successful, $failed failed out of $count total"
    return $failed
}

# Usage help
show_usage() {
    cat << EOF
FFmpeg Profiles for SoVAni Crosspost v2.1

USAGE:
    source $0  # Load functions into shell

FUNCTIONS:
    to_9x16 INPUT OUTPUT [STRATEGY] [BG_COLOR]     # Instagram Stories, TikTok
    to_4x5 INPUT OUTPUT [STRATEGY] [BG_COLOR]      # Instagram Feed
    to_1x1 INPUT OUTPUT [STRATEGY] [BG_COLOR]      # Instagram Square
    to_16x9 INPUT OUTPUT [STRATEGY] [BG_COLOR]     # YouTube, VK, Facebook
    to_custom INPUT OUTPUT WIDTH HEIGHT [STRATEGY] [BG_COLOR]
    
    get_aspect_info INPUT                          # Show aspect ratio info
    batch_convert INPUT_DIR OUTPUT_DIR RATIO [STRATEGY] [QUALITY]

STRATEGIES:
    pad     - Add padding (default, no distortion)
    crop    - Crop to fit (may lose content)
    stretch - Stretch to fit (may distort)

BACKGROUND COLORS:
    black, white, gray, blue, red, green, etc.

QUALITY PROFILES:
    high, medium (default), low, web

EXAMPLES:
    to_9x16 input.mp4 output_stories.mp4 pad black
    to_4x5 input.mp4 output_feed.mp4 crop
    batch_convert ./videos ./output 16x9 pad medium
    get_aspect_info input.mp4

EOF
}

# Check if script is being executed directly
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    if [[ $# -eq 0 ]]; then
        show_usage
        exit 0
    fi
    
    # Allow direct execution of functions
    "$@"
fi