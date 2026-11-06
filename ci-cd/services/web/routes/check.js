const express = require('express');
const mysql = require('mysql2/promise');
const axios = require('axios');
const router = express.Router();

// ══════════════════════════════════════════════
//  GET /check/db  —  MySQL 연결 확인
// ══════════════════════════════════════════════
router.get('/db', async (req, res) => {
  let connection;
  try {
    connection = await mysql.createConnection({
      host:     process.env.DB_HOST,
      port:     Number(process.env.DB_PORT),
      user:     process.env.DB_USER,
      password: process.env.DB_PASSWORD,
      database: process.env.DB_NAME,
      connectTimeout: 5000,   // 5초 안에 안 되면 실패 처리
    });

    await connection.ping();

    res.json({
      status:  'ok',
      message: 'DB 서버 연결 성공',
      host:    process.env.DB_HOST,
      port:    process.env.DB_PORT,
      db:      process.env.DB_NAME,
    });

  } catch (err) {
    res.status(500).json({
      status:  'fail',
      message: 'DB 서버 연결 실패',
      host:    process.env.DB_HOST,
      port:    process.env.DB_PORT,
      error:   err.message,
    });
  } finally {
    if (connection) await connection.end();
  }
});

// ══════════════════════════════════════════════
//  GET /check/zabbix  —  Zabbix API 연결 확인
// ══════════════════════════════════════════════
router.get('/zabbix', async (req, res) => {
  const url = process.env.ZABBIX_URL;

  try {
    // Use the unauthenticated version endpoint so this diagnostic never
    // obtains or returns a Zabbix API session token.
    const response = await axios.post(
      url,
      {
        jsonrpc: '2.0',
        method:  'apiinfo.version',
        params: [],
        id: 1,
      },
      {
        headers: { 'Content-Type': 'application/json' },
        timeout: 5000,   // 5초 안에 안 되면 실패 처리
      }
    );

    const data = response.data;

    // Zabbix가 error 필드를 반환하면 연결 실패로 처리
    if (data.error) {
      return res.status(401).json({
        status:  'fail',
        message: 'Zabbix API 응답은 왔으나 인증 실패',
        error:   data.error.data,
      });
    }

    res.json({
      status:  'ok',
      message: 'Zabbix 서버 연결 성공',
      version: data.result,
    });

  } catch (err) {
    res.status(500).json({
      status:  'fail',
      message: 'Zabbix 서버 연결 실패',
      error:   err.message,
    });
  }
});

module.exports = router;
