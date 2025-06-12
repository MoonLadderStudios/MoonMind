# Args are added as specific containers to build and run
$specificContainers = ""
if ($args.Count -gt 0) {
    $specificContainers = $args
}

docker-compose -f docker-compose.dev.yaml down
docker-compose -f docker-compose.dev.yaml build $specificContainers
docker-compose -f docker-compose.dev.yaml up -d $specificContainers
docker-compose -f docker-compose.dev.yaml logs -f