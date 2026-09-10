# Testing

The operator provides the approved `sample-native-tests` Docker image source.
The generic managed entrypoint is:

```sh
moonmind container run --spec tools/automation-job.json --request-id verify-candidate-01-automation
```

Use the terminal job result and automation log/report artifacts as evidence.
A local compiler probe does not determine container service availability.
