import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const batchLatency = new Trend('batch_latency_ms');
const perItemLatency = new Trend('per_item_latency_ms');

const TEXTS = [
    "The pitcher threw a fastball and struck out the batter.",
    "NASA launched a new satellite to study Mars.",
    "The home run sealed the championship game.",
    "The telescope captured images of distant galaxies.",
    "The shortstop made an incredible diving catch.",
    "SpaceX successfully landed the rocket booster.",
    "The batting average this season is remarkable.",
    "The International Space Station orbits every 90 minutes.",
    "The catcher signaled for a curveball on the outside corner.",
    "Astronomers discovered a new exoplanet in the habitable zone.",
];

export const options = {
    stages: [
        { duration: '30s', target: 10 },   // ramp up to 10 users
        { duration: '1m',  target: 10 },   // sustain 10 users
        { duration: '30s', target: 0 },    // ramp down
    ],
    thresholds: {
        http_req_duration: ['p(95)<2000'],  // 95% of requests under 2s (batches are larger)
        errors: ['rate<0.1'],
    },
};

export default function () {
    const batchSize = 10;
    const texts = [];
    for (let i = 0; i < batchSize; i++) {
        texts.push(TEXTS[Math.floor(Math.random() * TEXTS.length)]);
    }

    const res = http.post(
        'http://model-api:8000/predict/batch',
        JSON.stringify({ texts: texts }),
        { headers: { 'Content-Type': 'application/json' } }
    );

    const success = check(res, {
        'status is 200': (r) => r.status === 200,
        'has predictions array': (r) => {
            try {
                const body = JSON.parse(r.body);
                return body.predictions && body.predictions.length === batchSize;
            } catch (e) {
                return false;
            }
        },
        'has batch_size': (r) => {
            try {
                return JSON.parse(r.body).batch_size === batchSize;
            } catch (e) {
                return false;
            }
        },
    });

    errorRate.add(!success);
    batchLatency.add(res.timings.duration);

    // Extract per-item latency from the response
    try {
        const body = JSON.parse(res.body);
        if (body.per_item_latency_ms) {
            perItemLatency.add(body.per_item_latency_ms);
        }
    } catch (e) {
        // ignore parse errors
    }

    sleep(0.1);
}
