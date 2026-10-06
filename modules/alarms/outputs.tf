output "alarm_arns" {
  description = "ARN of every alarm of the unit, by alarm key"
  value = merge(
    { for s, a in aws_cloudwatch_metric_alarm.service_cpu : "${s}-cpu" => a.arn },
    { for s, a in aws_cloudwatch_metric_alarm.healthy_hosts : "${s}-healthy-hosts" => a.arn },
    {
      "alb-5xx"                  = aws_cloudwatch_metric_alarm.alb_5xx.arn
      "target-5xx"               = aws_cloudwatch_metric_alarm.target_5xx.arn
      "db-free-storage"          = aws_cloudwatch_metric_alarm.db_free_storage.arn
      "db-connections"           = aws_cloudwatch_metric_alarm.db_connections.arn
      "db-cpu"                   = aws_cloudwatch_metric_alarm.db_cpu.arn
      "ingest-failed-invocation" = aws_cloudwatch_metric_alarm.ingest_failed_invocations.arn
    },
  )
}
