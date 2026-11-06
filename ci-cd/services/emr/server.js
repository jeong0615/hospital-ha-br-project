const express = require('express');
const bodyParser = require('body-parser');
const cors = require('cors');
const db = require('./backend/db');

const app = express();
app.use(cors());
app.use(bodyParser.json());

// API 라우터 연결
app.use('/api/auth', require('./backend/auth'));
app.use('/api/patients', require('./backend/patients'));
app.use('/api/medical-records', require('./backend/medical_records'));
app.use('/api/logs', require('./backend/logs'));

// Web appointments are stored separately from completed EMR visits. Expose
// only the fields already used by the Web reservation list; never return the
// reservation password, phone number, or free-text content to this view.
app.get('/api/reservations/recent', async (_req, res) => {
    try {
        const [reservations] = await db.query(
            `SELECT reservation_id, patient_name, department, title,
                    DATE_FORMAT(created_at, '%Y-%m-%d %H:%i') AS created_at
             FROM ReservationPosts
             ORDER BY reservation_id DESC
             LIMIT 10`
        );
        res.status(200).json({ success: true, data: reservations });
    } catch (error) {
        console.error('Web 예약 조회 오류:', error);
        res.status(500).json({ success: false, message: '예약 목록을 불러오지 못했습니다.' });
    }
});

// Dump DR's dedicated health path performs no patient query and returns no
// diagnostic details. Public access is separately authenticated at DR Web's
// Nginx origin proxy; the EMR app port itself remains loopback-published.
app.get('/__hospital_dr/health', async (_req, res) => {
    try {
        await db.query('SELECT 1');
        res.set('Cache-Control', 'no-store, private').status(204).end();
    } catch (_) {
        res.set('Cache-Control', 'no-store, private').status(503).end();
    }
});

// 정적 파일 서빙 (프론트엔드 HTML, CSS, JS)
app.use(express.static('.'));

const PORT = process.env.PORT || 3000;

app.listen(PORT, '0.0.0.0', () => {
    console.log(`EMR 서버가 http://0.0.0.0:${PORT} 에서 실행 중입니다.`);
});
