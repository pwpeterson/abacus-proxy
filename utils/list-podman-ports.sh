#!/bin/bash

echo "Podman Containers - Port Mappings"
echo "=========================================="
printf "%-30s %-25s %-30s\n" "CONTAINER NAME" "INTERNAL PORT" "EXTERNAL PORT"
echo "--------------------------------------------------------------------------------------------------------"

# Get list of running containers
podman ps --format "{{.Names}}" | while read -r container; do
    # Get port mappings for this container
    ports=$(podman port "$container" 2>/dev/null)

    if [ -n "$ports" ]; then
        # Container has port mappings
        first_line=true
        echo "$ports" | while IFS= read -r line; do
            # Parse the port mapping (format: internal_port/protocol -> external_ip:external_port)
            internal=$(echo "$line" | awk '{print $1}')
            external=$(echo "$line" | awk '{print $3}')

            if [ "$first_line" = true ]; then
                printf "%-30s %-25s %-30s\n" "$container" "$internal" "$external"
                first_line=false
            else
                printf "%-30s %-25s %-30s\n" "" "$internal" "$external"
            fi
        done
    else
        # Container has no port mappings
        printf "%-30s %-25s %-30s\n" "$container" "(no ports exposed)" ""
    fi
done

echo ""
echo "Summary:"
echo "--------"
echo "Total containers: $(podman ps -q | wc -l)"
echo "Containers with ports: $(podman ps --format '{{.Names}}' | while read c; do podman port "$c" 2>/dev/null; done | grep -c .)"
