#!/bin/bash

# 台股量化交易倉庫自動下載腳本
# 使用方式: bash download_repos.sh

set -e

# 顏色定義
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# 圖示
CHECKMARK="${GREEN}✓${NC}"
CROSS="${RED}✗${NC}"
INFO="${BLUE}ℹ${NC}"
LOADING="${YELLOW}⏳${NC}"

# 當前目錄
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPOS_DIR="$SCRIPT_DIR/github-repos"

echo -e "${BLUE}╔════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  台股量化交易 GitHub 倉庫自動下載工具    ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════╝${NC}\n"

# 檢查必要工具
echo -e "${INFO} 檢查必要工具..."

if ! command -v git &> /dev/null; then
    echo -e "${CROSS} Git 未安裝，請先安裝 Git"
    exit 1
fi
echo -e "${CHECKMARK} Git 已安裝"

if ! command -v wget &> /dev/null && ! command -v curl &> /dev/null; then
    echo -e "${CROSS} wget 或 curl 未安裝"
    exit 1
fi

DOWNLOAD_CMD=""
if command -v wget &> /dev/null; then
    DOWNLOAD_CMD="wget -q"
    echo -e "${CHECKMARK} 使用 wget 下載"
else
    DOWNLOAD_CMD="curl -L -o"
    echo -e "${CHECKMARK} 使用 curl 下載"
fi

if ! command -v unzip &> /dev/null; then
    echo -e "${CROSS} unzip 未安裝，請先安裝 unzip"
    exit 1
fi
echo -e "${CHECKMARK} unzip 已安裝\n"

# 建立目錄
if [ -d "$REPOS_DIR" ]; then
    echo -e "${YELLOW}⚠ $REPOS_DIR 已存在，將清空重建${NC}"
    rm -rf "$REPOS_DIR"
fi

mkdir -p "$REPOS_DIR"
cd "$REPOS_DIR"

echo -e "${BLUE}開始下載倉庫...${NC}\n"

# 定義倉庫
declare -A REPOS=(
    ["1_twstock"]="https://github.com/mlouielu/twstock/archive/refs/heads/master.zip"
    ["2_FinMind"]="https://github.com/FinMind/FinMind/archive/refs/heads/main.zip"
    ["3_node-twstock"]="https://github.com/chunkai1312/node-twstock/archive/refs/heads/main.zip"
    ["4_Taiwan-Stock-Knowledge-Graph"]="https://github.com/jojowither/Taiwan-Stock-Knowledge-Graph/archive/refs/heads/main.zip"
    ["5_qlib"]="https://github.com/microsoft/qlib/archive/refs/heads/main.zip"
    ["6_FinRL"]="https://github.com/AI4Finance-Foundation/FinRL/archive/refs/heads/main.zip"
)

TOTAL=${#REPOS[@]}
CURRENT=0
FAILED=0

# 下載函數
download_repo() {
    local name=$1
    local url=$2
    local count=$3

    CURRENT=$((CURRENT + 1))

    echo -e "${YELLOW}【$CURRENT/$TOTAL】${NC} 下載 $name..."

    local filename="${name}.zip"
    local temp_dir=$(mktemp -d)

    if [ "$DOWNLOAD_CMD" = "wget -q" ]; then
        if wget -q "$url" -O "$filename"; then
            echo -e "  ${CHECKMARK} 下載完成"
        else
            echo -e "  ${CROSS} 下載失敗"
            FAILED=$((FAILED + 1))
            return 1
        fi
    else
        if curl -L -o "$filename" "$url" 2>/dev/null; then
            echo -e "  ${CHECKMARK} 下載完成"
        else
            echo -e "  ${CROSS} 下載失敗"
            FAILED=$((FAILED + 1))
            return 1
        fi
    fi

    # 檢查檔案大小
    local filesize=$(stat -f%z "$filename" 2>/dev/null || stat -c%s "$filename" 2>/dev/null)

    if [ "$filesize" -lt 1000 ]; then
        echo -e "  ${CROSS} 檔案太小 ($filesize bytes)，可能下載失敗"
        rm -f "$filename"
        FAILED=$((FAILED + 1))
        return 1
    fi

    # 解壓
    echo -e "  ${LOADING} 解壓中..."
    if unzip -q "$filename" 2>/dev/null; then
        # 重命名頂層目錄
        local extracted_dir=$(unzip -l "$filename" | head -2 | tail -1 | awk '{print $NF}' | sed 's|/||')

        if [ -d "$extracted_dir" ]; then
            mv "$extracted_dir" "$name"
            rm -f "$filename"
            echo -e "  ${CHECKMARK} 解壓完成"
            return 0
        else
            echo -e "  ${CROSS} 解壓失敗"
            rm -f "$filename"
            FAILED=$((FAILED + 1))
            return 1
        fi
    else
        echo -e "  ${CROSS} 解壓失敗"
        rm -f "$filename"
        FAILED=$((FAILED + 1))
        return 1
    fi
}

# 使用 git clone 作為備選方案
clone_via_git() {
    local name=$1
    local url=$2

    CURRENT=$((CURRENT + 1))

    echo -e "${YELLOW}【$CURRENT/$TOTAL】${NC} Git 克隆 $name..."

    # 移除 .zip 後綴得到真實倉庫名稱
    local repo_name=$(echo $name | sed 's/^[0-9]*_//')

    # 從 URL 提取倉庫 URL（移除 /archive/...）
    local repo_url=$(echo $url | sed 's|/archive/.*||')

    if git clone --depth 1 "$repo_url" "$name" 2>/dev/null; then
        echo -e "  ${CHECKMARK} 克隆完成"
        return 0
    else
        echo -e "  ${CROSS} 克隆失敗"
        FAILED=$((FAILED + 1))
        return 1
    fi
}

# 逐個下載倉庫
for name in "${!REPOS[@]}"; do
    url="${REPOS[$name]}"
    download_repo "$name" "$url" $CURRENT || clone_via_git "$name" "$url"
    echo ""
done

# 最終統計
echo -e "${BLUE}╔════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  下載完成！${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════╝${NC}\n"

echo -e "${INFO} 總計：$TOTAL 個倉庫"
echo -e "${CHECKMARK} 成功：$((TOTAL - FAILED)) 個"

if [ $FAILED -gt 0 ]; then
    echo -e "${CROSS} 失敗：$FAILED 個"
    echo -e "\n${YELLOW}⚠ 部分倉庫下載失敗，請檢查網絡連接"
fi

echo -e "\n${INFO} 倉庫位置：$REPOS_DIR"
ls -lh "$REPOS_DIR" | tail -n +2

echo -e "\n${GREEN}✨ 下一步：${NC}"
echo -e "1. 進入 github-repos 目錄"
echo -e "2. 參考 01_快速開始指南.md 進行環境設置"
echo -e "3. 開始開發你的交易策略！\n"

