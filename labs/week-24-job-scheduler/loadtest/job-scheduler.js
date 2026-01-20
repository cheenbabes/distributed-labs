import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const jobCreationTrend = new Trend('job_creation_latency');
const jobStatusTrend = new Trend('job_status_latency');
const jobsCreated = new Counter('jobs_created');

// Test configuration
export const options = {
  scenarios: {
    // Scenario 1: Create immediate jobs
    immediate_jobs: {
      executor: 'constant-arrival-rate',
      rate: 5,
      timeUnit: '1s',
      duration: '2m',
      preAllocatedVUs: 10,
      maxVUs: 20,
      exec: 'createImmediateJob',
    },
    // Scenario 2: Create delayed jobs
    delayed_jobs: {
      executor: 'constant-arrival-rate',
      rate: 1,
      timeUnit: '1s',
      duration: '2m',
      preAllocatedVUs: 5,
      maxVUs: 10,
      startTime: '30s',
      exec: 'createDelayedJob',
    },
    // Scenario 3: Create failing jobs
    failing_jobs: {
      executor: 'constant-arrival-rate',
      rate: 1,
      timeUnit: '2s',
      duration: '2m',
      preAllocatedVUs: 5,
      maxVUs: 10,
      startTime: '1m',
      exec: 'createFailingJob',
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<1000'],
    errors: ['rate<0.1'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://scheduler:8000';

// Helper to create a job
function createJob(payload) {
  const res = http.post(
    `${BASE_URL}/jobs`,
    JSON.stringify(payload),
    {
      headers: { 'Content-Type': 'application/json' },
    }
  );

  jobCreationTrend.add(res.timings.duration);
  errorRate.add(res.status !== 200);

  const success = check(res, {
    'job created': (r) => r.status === 200,
    'job has id': (r) => r.json().id !== undefined,
  });

  if (success) {
    jobsCreated.add(1);
    return res.json();
  }
  return null;
}

// Scenario: Create immediate jobs
export function createImmediateJob() {
  const payload = {
    name: `load-test-immediate-${Date.now()}`,
    job_type: 'immediate',
    payload: {
      action: 'compute',
      iteration: __ITER,
    },
    execution_time_ms: Math.floor(Math.random() * 2000) + 500,
    fail_rate: 0.0,
    max_retries: 3,
    timeout_seconds: 30,
  };

  const job = createJob(payload);

  if (job) {
    // Optionally check status after a delay
    sleep(0.5);
    checkJobStatus(job.id);
  }

  sleep(0.1);
}

// Scenario: Create delayed jobs
export function createDelayedJob() {
  const delaySeconds = Math.floor(Math.random() * 10) + 5;

  const payload = {
    name: `load-test-delayed-${Date.now()}`,
    job_type: 'delayed',
    delay_seconds: delaySeconds,
    payload: {
      action: 'process_data',
      data_size: Math.floor(Math.random() * 1000),
    },
    execution_time_ms: 1000,
    fail_rate: 0.0,
    max_retries: 3,
    timeout_seconds: 60,
  };

  createJob(payload);
  sleep(0.5);
}

// Scenario: Create jobs that will fail and retry
export function createFailingJob() {
  const payload = {
    name: `load-test-failing-${Date.now()}`,
    job_type: 'immediate',
    payload: {
      action: 'compute',
      should_fail: true,
    },
    execution_time_ms: 500,
    fail_rate: 0.7,  // 70% chance of failure
    max_retries: 3,
    timeout_seconds: 30,
  };

  const job = createJob(payload);

  if (job) {
    // Track this job to see retries
    sleep(5);
    checkJobStatus(job.id);
  }

  sleep(1);
}

// Check job status
function checkJobStatus(jobId) {
  const res = http.get(`${BASE_URL}/jobs/${jobId}`);

  jobStatusTrend.add(res.timings.duration);

  check(res, {
    'status check ok': (r) => r.status === 200,
    'has status field': (r) => r.json().status !== undefined,
  });

  return res.json();
}

// Get scheduler stats
export function getStats() {
  const res = http.get(`${BASE_URL}/stats`);

  check(res, {
    'stats ok': (r) => r.status === 200,
    'has queue_length': (r) => r.json().queue_length !== undefined,
  });
}

export function handleSummary(data) {
  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
