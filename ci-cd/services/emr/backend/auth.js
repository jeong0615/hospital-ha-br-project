/**
 * backend/auth.js
 * 로그인 및 인증 처리 API 모듈
 */
const express = require('express');
const router = express.Router();
const db = require('./db'); // MySQL 커넥션 풀을 가져옴

// 로그인 API (POST /api/auth/login)
router.post('/login', async (req, res) => {
    const { login_id, password } = req.body;
    // 1. 유효성 검사
    if (!login_id || !password) {
        return res.status(400).json({ 
            success: false, 
            message: '아이디와 비밀번호를 모두 입력해주세요.' 
        });
    }
    try {
        // 2. DB에서 유저 정보 조회
        const [Users] = await db.query(
            'SELECT user_id, login_id, role, password FROM Users WHERE login_id = ?',
            [login_id]
        );

        if (Users.length === 0) {
            return res.status(401).json({ 
                success: false, 
                message: '아이디 또는 비밀번호를 확인해주세요.' 
            });
        }

        const user = Users[0];

        // 3. 비밀번호 검증
        if (user.password !== password) {
            return res.status(401).json({ 
                success: false, 
                message: '아이디 또는 비밀번호를 확인해주세요.' 
            });
        }

        // 4. 로그인 성공 시 접근 로그(AccessLogs) 기록
        const ipAddress = req.headers['x-forwarded-for'] || req.socket.remoteAddress;
        
        await db.query(
            'INSERT INTO AccessLogs (user_id, action_type, access_time, ip_address) VALUES (?, ?, NOW(), ?)',
            [user.user_id, 'LOGIN', ipAddress]
        );

        // 5. 프론트엔드 세션(sessionStorage)에 저장할 정보 반환 (보안상 비밀번호 제외)
        const userInfo = {
            user_id: user.user_id,
            login_id: user.login_id,
            name: user.login_id,
            role: user.role
        };

        return res.status(200).json({
            success: true,
            message: '로그인에 성공했습니다.',
            data: userInfo // { user_id, login_id, role, name }
        });

    } catch (error) {
        console.error('Login API Error:', error);
        return res.status(500).json({ 
            success: false, 
            message: '서버 내부 오류가 발생했습니다.' 
        });
    }
});

module.exports = router;
