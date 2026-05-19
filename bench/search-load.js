import http from 'k6/http';
import { check } from 'k6';
import exec from 'k6/execution';

// Each request uses a distinct query so the API OutputCache is bypassed
// and the benchmark measures real (cold) Elasticsearch search work.
const QUERIES = open('./queries.txt').split('\n').filter((q) => q.length > 0);

const BASE = __ENV.BASE_URL || 'http://localhost:62506';
const PATH = __ENV.SEARCH_PATH || '/api/v1/uk/elasticsearch/search';
const QUERY_PARAM = __ENV.QUERY_PARAM || 'query';
const LIMIT = __ENV.LIMIT || '20';
const VUS = parseInt(__ENV.VUS || '20');

export const options = {
  scenarios: {
    cold_search: {
      executor: 'shared-iterations',
      vus: VUS,
      iterations: QUERIES.length,
      maxDuration: '10m',
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<2000'],
  },
};

export default function () {
  const q = QUERIES[exec.scenario.iterationInTest % QUERIES.length];
  const url = `${BASE}${PATH}?${QUERY_PARAM}=${encodeURIComponent(q)}&limit=${LIMIT}`;
  const res = http.get(url);
  check(res, { 'status is 200': (r) => r.status === 200 });
}
