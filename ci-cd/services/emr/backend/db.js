/**
 * backend/db.js
 * MySQL 데이터베이스 연결 풀 설정
 */
const mysql = require('mysql2/promise');

const requiredEnvironment = ['DB_HOST', 'DB_USER', 'DB_PASSWORD', 'DB_NAME'];
const missingEnvironment = requiredEnvironment.filter(
    name => !Object.prototype.hasOwnProperty.call(process.env, name)
);

if (missingEnvironment.length > 0) {
    throw new Error(`Missing required database environment variables: ${missingEnvironment.join(', ')}`);
}

const dbPort = Number(process.env.DB_PORT || 3306);
if (!Number.isInteger(dbPort) || dbPort < 1 || dbPort > 65535) {
    throw new Error('DB_PORT must be a valid TCP port.');
}

const pool = mysql.createPool({
    host: process.env.DB_HOST,
    port: dbPort,
    user: process.env.DB_USER,
    password: process.env.DB_PASSWORD,
    database: process.env.DB_NAME,
    waitForConnections: true,
    connectionLimit: 10,         // 동시 연결 최대 수
    queueLimit: 0
});

// 서버 구동 시 초기 DB 연결 상태 확인용
pool.getConnection()
    .then(connection => {
        console.log('MySQL DB(hospital_db) 연결 성공!');
        connection.release();
    })
    .catch(err => {
        console.error('MySQL DB 연결 실패:', err.message);
    });

module.exports = pool;
