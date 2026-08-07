"""Phase 7: continuous AI-visibility monitoring.

A monitor scans a website on a schedule, keeps a full history, detects changes vs
the previous scan, raises alerts, and emails summaries. The scheduler (job queue +
worker) is deliberately separate from the scanning logic so scans run independently
of HTTP requests and a future distributed worker can drain the same queue.
"""
