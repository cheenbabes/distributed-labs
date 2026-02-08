import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const predictionLatency = new Trend('prediction_latency_ms');

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
        http_req_duration: ['p(95)<500'],  // 95% of requests under 500ms
        errors: ['rate<0.1'],              // error rate under 10%
    },
};

export default function () {
    const text = TEXTS[Math.floor(Math.random() * TEXTS.length)];

    const res = http.post(
        'http://model-api:8000/predict',
        JSON.stringify({ text: text }),
        { headers: { 'Content-Type': 'application/json' } }
    );

    const success = check(res, {
        'status is 200': (r) => r.status === 200,
        'has predicted_class': (r) => {
            try {
                return JSON.parse(r.body).predicted_class !== undefined;
            } catch (e) {
                return false;
            }
        },
    });

    errorRate.add(!success);
    predictionLatency.add(res.timings.duration);

    sleep(0.1);
}
