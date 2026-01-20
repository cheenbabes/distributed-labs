import http from 'k6/http';
import { sleep } from 'k6';
import { Trend, Gauge } from 'k6/metrics';

// Custom metrics for monitoring
const queueDepthTrend = new Trend('queue_depth_trend');
const queueMemoryTrend = new Trend('queue_memory_mb_trend');
const processMemoryTrend = new Trend('process_memory_mb_trend');

// Simple monitoring script - just watches metrics without adding load
export const options = {
  vus: 1,
  duration: '5m',
  thresholds: {
    // Alert if queue grows too large
    queue_depth_trend: ['max<5000'],
    queue_memory_mb_trend: ['max<200'],
  },
};

const PRODUCER_URL = __ENV.PRODUCER_URL || 'http://lab18-producer:8000';
const CONSUMER_URL = __ENV.CONSUMER_URL || 'http://lab18-consumer:8001';

export default function () {
  // Check producer status
  const producerRes = http.get(`${PRODUCER_URL}/status`);
  if (producerRes.status === 200) {
    const status = producerRes.json();

    queueDepthTrend.add(status.queue.size);
    queueMemoryTrend.add(status.queue.memory_mb);
    processMemoryTrend.add(status.process.memory_mb);

    console.log(
      `Queue: ${status.queue.size} items | ` +
      `Queue Memory: ${status.queue.memory_mb}MB | ` +
      `Process Memory: ${status.process.memory_mb}MB | ` +
      `Mode: ${status.queue.mode}`
    );
  }

  // Check consumer status
  const consumerRes = http.get(`${CONSUMER_URL}/status`);
  if (consumerRes.status === 200) {
    const status = consumerRes.json();
    console.log(
      `Consumer: processing_time=${status.config.processing_time_ms}ms | ` +
      `max_throughput=${status.state.max_throughput}`
    );
  }

  sleep(2); // Check every 2 seconds
}

export function handleSummary(data) {
  const summary = {
    queue_depth: {
      max: data.metrics.queue_depth_trend?.values?.max || 0,
      avg: data.metrics.queue_depth_trend?.values?.avg || 0,
    },
    queue_memory_mb: {
      max: data.metrics.queue_memory_mb_trend?.values?.max || 0,
      avg: data.metrics.queue_memory_mb_trend?.values?.avg || 0,
    },
    process_memory_mb: {
      max: data.metrics.process_memory_mb_trend?.values?.max || 0,
      avg: data.metrics.process_memory_mb_trend?.values?.avg || 0,
    },
  };

  console.log('\n=== Monitoring Summary ===');
  console.log(`Max Queue Depth: ${summary.queue_depth.max}`);
  console.log(`Avg Queue Depth: ${summary.queue_depth.avg.toFixed(2)}`);
  console.log(`Max Queue Memory: ${summary.queue_memory_mb.max.toFixed(2)}MB`);
  console.log(`Max Process Memory: ${summary.process_memory_mb.max.toFixed(2)}MB`);

  return {
    stdout: JSON.stringify(summary, null, 2),
  };
}
