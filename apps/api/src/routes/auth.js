import bcrypt from "bcrypt";
import express from "express";
import jwt from "jsonwebtoken";
import validator from "validator";

import { config } from "../config.js";
import { prisma } from "../prisma.js";
import { cleanText } from "../utils/sanitize.js";

export const authRouter = express.Router();

function createToken(user) {
  return jwt.sign(
    {
      sub: user.id,
      email: user.email
    },
    config.jwtSecret,
    { expiresIn: config.jwtExpiresIn }
  );
}

function publicUser(user) {
  return {
    id: user.id,
    email: user.email,
    credits: user.credits,
    createdAt: user.createdAt
  };
}

authRouter.post("/register", async (req, res, next) => {
  try {
    const email = cleanText(req.body.email).toLowerCase();
    const password = String(req.body.password || "");

    if (!validator.isEmail(email)) {
      return res.status(400).json({ message: "A valid email is required." });
    }

    if (password.length < 8) {
      return res.status(400).json({ message: "Password must be at least 8 characters." });
    }

    const existing = await prisma.user.findUnique({ where: { email } });
    if (existing) {
      return res.status(409).json({ message: "An account already exists for this email." });
    }

    const hashedPassword = await bcrypt.hash(password, 12);
    const user = await prisma.user.create({
      data: {
        email,
        password: hashedPassword
      }
    });

    return res.status(201).json({
      token: createToken(user),
      user: publicUser(user)
    });
  } catch (error) {
    return next(error);
  }
});

authRouter.post("/login", async (req, res, next) => {
  try {
    const email = cleanText(req.body.email).toLowerCase();
    const password = String(req.body.password || "");

    if (!validator.isEmail(email) || !password) {
      return res.status(400).json({ message: "Email and password are required." });
    }

    const user = await prisma.user.findUnique({ where: { email } });
    if (!user) {
      return res.status(401).json({ message: "Invalid credentials." });
    }

    const passwordMatches = await bcrypt.compare(password, user.password);
    if (!passwordMatches) {
      return res.status(401).json({ message: "Invalid credentials." });
    }

    return res.json({
      token: createToken(user),
      user: publicUser(user)
    });
  } catch (error) {
    return next(error);
  }
});

export default authRouter;
