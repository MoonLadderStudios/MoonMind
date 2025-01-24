# Args are added as specific containers to build and run
$specificContainers = ""
if ($args.Count -gt 0) {
    $specificContainers = $args
}

docker-compose down
docker-compose up -d $specificContainers
docker-compose logs -f