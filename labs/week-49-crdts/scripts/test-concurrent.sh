#!/bin/bash
# Test script for concurrent CRDT updates
# This script simulates multiple clients making concurrent updates to different replicas

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

REPLICA_1="http://localhost:8001"
REPLICA_2="http://localhost:8002"
REPLICA_3="http://localhost:8003"
CLIENT="http://localhost:8020"
SYNC="http://localhost:8010"

echo -e "${BLUE}================================================${NC}"
echo -e "${BLUE}  CRDT Concurrent Update Test Suite${NC}"
echo -e "${BLUE}================================================${NC}"
echo ""

# Wait for services to be ready
wait_for_services() {
    echo -e "${YELLOW}Waiting for services to be ready...${NC}"
    for i in {1..30}; do
        if curl -s "$REPLICA_1/health" > /dev/null 2>&1 && \
           curl -s "$REPLICA_2/health" > /dev/null 2>&1 && \
           curl -s "$REPLICA_3/health" > /dev/null 2>&1 && \
           curl -s "$CLIENT/health" > /dev/null 2>&1 && \
           curl -s "$SYNC/health" > /dev/null 2>&1; then
            echo -e "${GREEN}All services are ready!${NC}"
            return 0
        fi
        sleep 1
        echo -n "."
    done
    echo -e "${RED}Services not ready after 30 seconds${NC}"
    return 1
}

# Test 1: G-Counter concurrent increments
test_gcounter_concurrent() {
    echo ""
    echo -e "${BLUE}=== Test 1: G-Counter Concurrent Increments ===${NC}"

    COUNTER_NAME="test-gcounter-$(date +%s)"

    echo -e "${YELLOW}Incrementing counter '$COUNTER_NAME' on all replicas concurrently...${NC}"

    # Increment on each replica 10 times concurrently
    for i in {1..10}; do
        curl -s -X POST "$REPLICA_1/gcounter/$COUNTER_NAME/increment" -H "Content-Type: application/json" -d '{"amount": 1}' > /dev/null &
        curl -s -X POST "$REPLICA_2/gcounter/$COUNTER_NAME/increment" -H "Content-Type: application/json" -d '{"amount": 1}' > /dev/null &
        curl -s -X POST "$REPLICA_3/gcounter/$COUNTER_NAME/increment" -H "Content-Type: application/json" -d '{"amount": 1}' > /dev/null &
    done
    wait

    echo -e "${YELLOW}Getting values from each replica before sync...${NC}"

    echo -n "Replica 1: "
    curl -s "$REPLICA_1/gcounter/$COUNTER_NAME" | jq -r '.value'

    echo -n "Replica 2: "
    curl -s "$REPLICA_2/gcounter/$COUNTER_NAME" | jq -r '.value'

    echo -n "Replica 3: "
    curl -s "$REPLICA_3/gcounter/$COUNTER_NAME" | jq -r '.value'

    echo -e "${YELLOW}Triggering sync...${NC}"
    curl -s -X POST "$SYNC/sync" > /dev/null
    sleep 2

    echo -e "${YELLOW}Getting values after sync...${NC}"

    VAL1=$(curl -s "$REPLICA_1/gcounter/$COUNTER_NAME" | jq -r '.value')
    VAL2=$(curl -s "$REPLICA_2/gcounter/$COUNTER_NAME" | jq -r '.value')
    VAL3=$(curl -s "$REPLICA_3/gcounter/$COUNTER_NAME" | jq -r '.value')

    echo "Replica 1: $VAL1"
    echo "Replica 2: $VAL2"
    echo "Replica 3: $VAL3"

    if [ "$VAL1" == "$VAL2" ] && [ "$VAL2" == "$VAL3" ] && [ "$VAL1" == "30" ]; then
        echo -e "${GREEN}PASSED: All replicas converged to expected value (30)${NC}"
    else
        echo -e "${RED}FAILED: Values do not match or not 30${NC}"
    fi
}

# Test 2: PN-Counter concurrent increments and decrements
test_pncounter_concurrent() {
    echo ""
    echo -e "${BLUE}=== Test 2: PN-Counter Concurrent Inc/Dec ===${NC}"

    COUNTER_NAME="test-pncounter-$(date +%s)"

    echo -e "${YELLOW}Incrementing and decrementing counter '$COUNTER_NAME' concurrently...${NC}"

    # Replica 1: increment 15 times
    for i in {1..15}; do
        curl -s -X POST "$REPLICA_1/pncounter/$COUNTER_NAME/increment" -H "Content-Type: application/json" -d '{"amount": 1}' > /dev/null &
    done

    # Replica 2: increment 10 times, decrement 5 times
    for i in {1..10}; do
        curl -s -X POST "$REPLICA_2/pncounter/$COUNTER_NAME/increment" -H "Content-Type: application/json" -d '{"amount": 1}' > /dev/null &
    done
    for i in {1..5}; do
        curl -s -X POST "$REPLICA_2/pncounter/$COUNTER_NAME/decrement" -H "Content-Type: application/json" -d '{"amount": 1}' > /dev/null &
    done

    # Replica 3: decrement 10 times
    for i in {1..10}; do
        curl -s -X POST "$REPLICA_3/pncounter/$COUNTER_NAME/decrement" -H "Content-Type: application/json" -d '{"amount": 1}' > /dev/null &
    done

    wait

    echo -e "${YELLOW}Getting values before sync...${NC}"
    echo "Replica 1: $(curl -s "$REPLICA_1/pncounter/$COUNTER_NAME" | jq -r '.value')"
    echo "Replica 2: $(curl -s "$REPLICA_2/pncounter/$COUNTER_NAME" | jq -r '.value')"
    echo "Replica 3: $(curl -s "$REPLICA_3/pncounter/$COUNTER_NAME" | jq -r '.value')"

    echo -e "${YELLOW}Triggering sync...${NC}"
    curl -s -X POST "$SYNC/sync" > /dev/null
    sleep 2

    echo -e "${YELLOW}Getting values after sync...${NC}"

    VAL1=$(curl -s "$REPLICA_1/pncounter/$COUNTER_NAME" | jq -r '.value')
    VAL2=$(curl -s "$REPLICA_2/pncounter/$COUNTER_NAME" | jq -r '.value')
    VAL3=$(curl -s "$REPLICA_3/pncounter/$COUNTER_NAME" | jq -r '.value')

    echo "Replica 1: $VAL1"
    echo "Replica 2: $VAL2"
    echo "Replica 3: $VAL3"

    # Expected: +15 +10 -5 -10 = 10
    if [ "$VAL1" == "$VAL2" ] && [ "$VAL2" == "$VAL3" ] && [ "$VAL1" == "10" ]; then
        echo -e "${GREEN}PASSED: All replicas converged to expected value (10)${NC}"
    else
        echo -e "${RED}FAILED: Values do not match or not 10${NC}"
    fi
}

# Test 3: LWW-Register concurrent writes
test_lww_concurrent() {
    echo ""
    echo -e "${BLUE}=== Test 3: LWW-Register Concurrent Writes ===${NC}"

    REGISTER_NAME="test-lww-$(date +%s)"

    echo -e "${YELLOW}Writing to register '$REGISTER_NAME' from all replicas...${NC}"

    # Write from each replica with slight delays to create clear ordering
    curl -s -X POST "$REPLICA_1/lww/$REGISTER_NAME/set" -H "Content-Type: application/json" -d '{"value": "replica-1-value"}' > /dev/null &
    sleep 0.05
    curl -s -X POST "$REPLICA_2/lww/$REGISTER_NAME/set" -H "Content-Type: application/json" -d '{"value": "replica-2-value"}' > /dev/null &
    sleep 0.05
    curl -s -X POST "$REPLICA_3/lww/$REGISTER_NAME/set" -H "Content-Type: application/json" -d '{"value": "replica-3-value"}' > /dev/null &

    wait

    echo -e "${YELLOW}Getting values before sync...${NC}"
    echo "Replica 1: $(curl -s "$REPLICA_1/lww/$REGISTER_NAME" | jq -r '.current.value')"
    echo "Replica 2: $(curl -s "$REPLICA_2/lww/$REGISTER_NAME" | jq -r '.current.value')"
    echo "Replica 3: $(curl -s "$REPLICA_3/lww/$REGISTER_NAME" | jq -r '.current.value')"

    echo -e "${YELLOW}Triggering sync...${NC}"
    curl -s -X POST "$SYNC/sync" > /dev/null
    sleep 2

    echo -e "${YELLOW}Getting values after sync...${NC}"

    VAL1=$(curl -s "$REPLICA_1/lww/$REGISTER_NAME" | jq -r '.current.value')
    VAL2=$(curl -s "$REPLICA_2/lww/$REGISTER_NAME" | jq -r '.current.value')
    VAL3=$(curl -s "$REPLICA_3/lww/$REGISTER_NAME" | jq -r '.current.value')

    echo "Replica 1: $VAL1"
    echo "Replica 2: $VAL2"
    echo "Replica 3: $VAL3"

    if [ "$VAL1" == "$VAL2" ] && [ "$VAL2" == "$VAL3" ]; then
        echo -e "${GREEN}PASSED: All replicas converged to same value ($VAL1)${NC}"
    else
        echo -e "${RED}FAILED: Values do not match${NC}"
    fi
}

# Test 4: Network partition and merge
test_partition_merge() {
    echo ""
    echo -e "${BLUE}=== Test 4: Network Partition and Merge ===${NC}"

    COUNTER_NAME="test-partition-$(date +%s)"

    echo -e "${YELLOW}Initial setup: increment on all replicas...${NC}"
    curl -s -X POST "$REPLICA_1/gcounter/$COUNTER_NAME/increment" -H "Content-Type: application/json" -d '{"amount": 5}' > /dev/null
    curl -s -X POST "$REPLICA_2/gcounter/$COUNTER_NAME/increment" -H "Content-Type: application/json" -d '{"amount": 5}' > /dev/null
    curl -s -X POST "$REPLICA_3/gcounter/$COUNTER_NAME/increment" -H "Content-Type: application/json" -d '{"amount": 5}' > /dev/null

    curl -s -X POST "$SYNC/sync" > /dev/null
    sleep 1

    echo "Initial value: $(curl -s "$REPLICA_1/gcounter/$COUNTER_NAME" | jq -r '.value')"

    echo -e "${YELLOW}Partitioning replica-1...${NC}"
    curl -s -X POST "$REPLICA_1/admin/partition" -H "Content-Type: application/json" -d '{"enabled": true}' > /dev/null

    echo -e "${YELLOW}Making updates during partition...${NC}"
    # Update partitioned replica
    curl -s -X POST "$REPLICA_1/gcounter/$COUNTER_NAME/increment" -H "Content-Type: application/json" -d '{"amount": 10}' > /dev/null

    # Update other replicas
    curl -s -X POST "$REPLICA_2/gcounter/$COUNTER_NAME/increment" -H "Content-Type: application/json" -d '{"amount": 20}' > /dev/null
    curl -s -X POST "$REPLICA_3/gcounter/$COUNTER_NAME/increment" -H "Content-Type: application/json" -d '{"amount": 30}' > /dev/null

    # Sync (will not include partitioned replica)
    curl -s -X POST "$SYNC/sync" > /dev/null
    sleep 1

    echo -e "${YELLOW}Values during partition:${NC}"
    echo "Replica 1 (partitioned): $(curl -s "$REPLICA_1/gcounter/$COUNTER_NAME" | jq -r '.value')"
    echo "Replica 2: $(curl -s "$REPLICA_2/gcounter/$COUNTER_NAME" | jq -r '.value')"
    echo "Replica 3: $(curl -s "$REPLICA_3/gcounter/$COUNTER_NAME" | jq -r '.value')"

    echo -e "${YELLOW}Healing partition...${NC}"
    curl -s -X POST "$REPLICA_1/admin/partition" -H "Content-Type: application/json" -d '{"enabled": false}' > /dev/null

    echo -e "${YELLOW}Triggering sync after heal...${NC}"
    curl -s -X POST "$SYNC/sync" > /dev/null
    sleep 2

    echo -e "${YELLOW}Final values after merge:${NC}"

    VAL1=$(curl -s "$REPLICA_1/gcounter/$COUNTER_NAME" | jq -r '.value')
    VAL2=$(curl -s "$REPLICA_2/gcounter/$COUNTER_NAME" | jq -r '.value')
    VAL3=$(curl -s "$REPLICA_3/gcounter/$COUNTER_NAME" | jq -r '.value')

    echo "Replica 1: $VAL1"
    echo "Replica 2: $VAL2"
    echo "Replica 3: $VAL3"

    # Expected: 5+5+5 (initial) + 10+20+30 (during partition) = 75
    if [ "$VAL1" == "$VAL2" ] && [ "$VAL2" == "$VAL3" ] && [ "$VAL1" == "75" ]; then
        echo -e "${GREEN}PASSED: All replicas converged after partition heal (75)${NC}"
    else
        echo -e "${RED}FAILED: Values do not match or not 75${NC}"
    fi
}

# Test 5: High concurrency stress test
test_stress() {
    echo ""
    echo -e "${BLUE}=== Test 5: High Concurrency Stress Test ===${NC}"

    COUNTER_NAME="test-stress-$(date +%s)"
    OPS_PER_REPLICA=50

    echo -e "${YELLOW}Running $OPS_PER_REPLICA concurrent increments on each replica...${NC}"

    start_time=$(date +%s.%N)

    for i in $(seq 1 $OPS_PER_REPLICA); do
        curl -s -X POST "$REPLICA_1/gcounter/$COUNTER_NAME/increment" -H "Content-Type: application/json" -d '{"amount": 1}' > /dev/null &
        curl -s -X POST "$REPLICA_2/gcounter/$COUNTER_NAME/increment" -H "Content-Type: application/json" -d '{"amount": 1}' > /dev/null &
        curl -s -X POST "$REPLICA_3/gcounter/$COUNTER_NAME/increment" -H "Content-Type: application/json" -d '{"amount": 1}' > /dev/null &
    done
    wait

    end_time=$(date +%s.%N)
    duration=$(echo "$end_time - $start_time" | bc)

    echo "Total operations: $((OPS_PER_REPLICA * 3))"
    echo "Duration: ${duration}s"

    echo -e "${YELLOW}Triggering sync...${NC}"
    curl -s -X POST "$SYNC/sync" > /dev/null
    sleep 2

    VAL1=$(curl -s "$REPLICA_1/gcounter/$COUNTER_NAME" | jq -r '.value')
    VAL2=$(curl -s "$REPLICA_2/gcounter/$COUNTER_NAME" | jq -r '.value')
    VAL3=$(curl -s "$REPLICA_3/gcounter/$COUNTER_NAME" | jq -r '.value')

    echo "Replica 1: $VAL1"
    echo "Replica 2: $VAL2"
    echo "Replica 3: $VAL3"

    EXPECTED=$((OPS_PER_REPLICA * 3))
    if [ "$VAL1" == "$VAL2" ] && [ "$VAL2" == "$VAL3" ] && [ "$VAL1" == "$EXPECTED" ]; then
        echo -e "${GREEN}PASSED: All replicas converged to expected value ($EXPECTED)${NC}"
    else
        echo -e "${RED}FAILED: Values do not match or not $EXPECTED${NC}"
    fi
}

# Run all tests
main() {
    wait_for_services

    test_gcounter_concurrent
    test_pncounter_concurrent
    test_lww_concurrent
    test_partition_merge
    test_stress

    echo ""
    echo -e "${BLUE}================================================${NC}"
    echo -e "${BLUE}  All tests completed!${NC}"
    echo -e "${BLUE}================================================${NC}"
}

# Run specific test or all
case "${1:-all}" in
    gcounter)
        wait_for_services && test_gcounter_concurrent
        ;;
    pncounter)
        wait_for_services && test_pncounter_concurrent
        ;;
    lww)
        wait_for_services && test_lww_concurrent
        ;;
    partition)
        wait_for_services && test_partition_merge
        ;;
    stress)
        wait_for_services && test_stress
        ;;
    all|*)
        main
        ;;
esac
