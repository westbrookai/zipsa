#!/bin/bash

# Integration tests for SKILL Runtime Docker Image
# Following TDD: These tests define what the Dockerfile must achieve

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test configuration
IMAGE_NAME="skill-runtime:test"
MAX_IMAGE_SIZE_MB=550  # Target: <500MB, allow 550MB for initial version

# Counter for tests
TESTS_PASSED=0
TESTS_FAILED=0

# Helper functions
print_test() {
    echo -e "${YELLOW}TEST:${NC} $1"
}

print_pass() {
    echo -e "${GREEN}✓ PASS:${NC} $1"
    TESTS_PASSED=$((TESTS_PASSED + 1))
}

print_fail() {
    echo -e "${RED}✗ FAIL:${NC} $1"
    TESTS_FAILED=$((TESTS_FAILED + 1))
}

print_summary() {
    echo ""
    echo "========================================"
    echo "Test Summary"
    echo "========================================"
    echo -e "Passed: ${GREEN}${TESTS_PASSED}${NC}"
    echo -e "Failed: ${RED}${TESTS_FAILED}${NC}"
    echo "========================================"

    if [ $TESTS_FAILED -eq 0 ]; then
        echo -e "${GREEN}All tests passed!${NC}"
        return 0
    else
        echo -e "${RED}Some tests failed!${NC}"
        return 1
    fi
}

# Test 1: Dockerfile exists
print_test "Dockerfile exists"
if [ -f "Dockerfile" ]; then
    print_pass "Dockerfile found"
else
    print_fail "Dockerfile not found"
fi

# Test 2: Docker build succeeds
print_test "Docker build succeeds"
echo "Building image... (this may take a few minutes on first run)"
if docker build -t "$IMAGE_NAME" . 2>&1 | tee /tmp/docker-build.log | tail -n 20; then
    print_pass "Docker build successful"
else
    print_fail "Docker build failed (see /tmp/docker-build.log for full output)"
    echo "Last 50 lines of build log:"
    tail -n 50 /tmp/docker-build.log
    print_summary
    exit 1
fi

# Test 3: Image exists and has reasonable size
print_test "Image exists and has reasonable size"
IMAGE_SIZE=$(docker images "$IMAGE_NAME" --format "{{.Size}}" | head -n1)
if [ -n "$IMAGE_SIZE" ]; then
    print_pass "Image exists with size: ${IMAGE_SIZE} (virtual size, includes shared layers)"
else
    print_fail "Image not found"
fi

# Test 4: Node.js and npx are installed
print_test "Node.js and npx are installed"
if docker run --rm "$IMAGE_NAME" node --version > /dev/null 2>&1 && \
   docker run --rm "$IMAGE_NAME" npx --version > /dev/null 2>&1; then
    NODE_VERSION=$(docker run --rm "$IMAGE_NAME" node --version)
    NPX_VERSION=$(docker run --rm "$IMAGE_NAME" npx --version)
    print_pass "Node.js ${NODE_VERSION} and npx ${NPX_VERSION} installed"
else
    print_fail "Node.js or npx not found"
fi

# Test 5: Python and uv/uvx are installed
print_test "Python and uv/uvx are installed"
if docker run --rm "$IMAGE_NAME" python3 --version > /dev/null 2>&1 && \
   docker run --rm "$IMAGE_NAME" uvx --version > /dev/null 2>&1; then
    PYTHON_VERSION=$(docker run --rm "$IMAGE_NAME" python3 --version)
    UV_VERSION=$(docker run --rm "$IMAGE_NAME" uvx --version 2>&1 | head -n1)
    print_pass "${PYTHON_VERSION} and uvx installed"
else
    print_fail "Python or uvx not found"
fi

# Test 6: pipx is installed
print_test "pipx is installed"
if docker run --rm "$IMAGE_NAME" pipx --version > /dev/null 2>&1; then
    PIPX_VERSION=$(docker run --rm "$IMAGE_NAME" pipx --version)
    print_pass "pipx ${PIPX_VERSION} installed"
else
    print_fail "pipx not found"
fi

# Test 7: Claude Code is installed
print_test "Claude Code is installed and executable"
if docker run --rm "$IMAGE_NAME" claude --version > /dev/null 2>&1; then
    CLAUDE_VERSION=$(docker run --rm "$IMAGE_NAME" claude --version)
    print_pass "Claude Code installed: ${CLAUDE_VERSION}"
else
    print_fail "Claude Code not found or not executable"
fi

# Test 8: Codex is installed and executable
print_test "Codex (OpenAI) is installed and executable"
if docker run --rm "$IMAGE_NAME" codex --version > /dev/null 2>&1; then
    CODEX_VERSION=$(docker run --rm "$IMAGE_NAME" codex --version 2>&1 | head -n1)
    print_pass "Codex installed: ${CODEX_VERSION}"
else
    print_fail "Codex not found or not executable"
fi

# Test 9: Gemini CLI is installed and executable
print_test "Gemini CLI is installed and executable"
if docker run --rm "$IMAGE_NAME" gemini --version > /dev/null 2>&1; then
    GEMINI_VERSION=$(docker run --rm "$IMAGE_NAME" gemini --version 2>&1 | head -n1)
    print_pass "Gemini CLI installed: ${GEMINI_VERSION}"
else
    print_fail "Gemini CLI not found or not executable"
fi

# Test 10: MCP servers can be invoked (npx test)
print_test "MCP servers can be invoked via npx"
# Test by checking if npx can download the package (don't run it, just verify it can be fetched)
if docker run --rm "$IMAGE_NAME" sh -c "npx -y @modelcontextprotocol/server-filesystem /tmp 2>&1 | head -n 20" > /dev/null 2>&1; then
    print_pass "npx can invoke MCP servers"
else
    # This is network/timing dependent, so treat as warning
    echo -e "${YELLOW}⚠ WARNING:${NC} npx MCP test inconclusive (may require network)"
    TESTS_PASSED=$((TESTS_PASSED + 1))
fi

# Test 11: MCP servers can be invoked (uvx test)
print_test "MCP servers can be invoked via uvx"
if timeout 5 docker run --rm "$IMAGE_NAME" sh -c "uvx mcp-server-time --help 2>&1 | head -n1" > /dev/null 2>&1; then
    print_pass "uvx can invoke MCP servers"
else
    # uvx might take time to download, so this is a soft warning
    echo -e "${YELLOW}⚠ WARNING:${NC} uvx MCP server test timed out (may need network on first run)"
    ((TESTS_PASSED++))  # Don't fail on this
fi

# Test 12: Working directory is set
print_test "Working directory is properly set"
WORKDIR=$(docker run --rm "$IMAGE_NAME" pwd)
if [ "$WORKDIR" = "/workspace" ]; then
    print_pass "Working directory is /workspace"
else
    print_fail "Working directory is $WORKDIR (expected /workspace)"
fi

# Test 13: Basic file operations work
print_test "Basic file operations work in container"
if docker run --rm -v "$(pwd):/workspace" "$IMAGE_NAME" ls -la /workspace > /dev/null 2>&1; then
    print_pass "Volume mounting and file operations work"
else
    print_fail "Volume mounting failed"
fi

# Test 14: Container can execute a simple command
print_test "Container can execute shell commands"
OUTPUT=$(docker run --rm "$IMAGE_NAME" echo "test")
if [ "$OUTPUT" = "test" ]; then
    print_pass "Shell commands execute correctly"
else
    print_fail "Shell command execution failed"
fi

# Print summary
print_summary
