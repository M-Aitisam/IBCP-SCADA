// packages/dashboard/src/context/AuthContext.tsx
'use client'

import { createContext, useContext, useState, useEffect, ReactNode } from 'react'
import { useRouter } from 'next/navigation'
import axios from 'axios'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1'

// Helper functions for sessionStorage
const TOKEN_KEY = 'access_token'
const USER_KEY = 'user'

const getToken = () => sessionStorage.getItem(TOKEN_KEY)
const setToken = (token: string) => sessionStorage.setItem(TOKEN_KEY, token)
const removeToken = () => sessionStorage.removeItem(TOKEN_KEY)

const getUser = () => {
  const user = sessionStorage.getItem(USER_KEY)
  return user ? JSON.parse(user) : null
}
const setUser = (user: any) => sessionStorage.setItem(USER_KEY, JSON.stringify(user))
const removeUser = () => sessionStorage.removeItem(USER_KEY)

interface User {
  id: string
  username: string
  email: string
  full_name: string
  role: string
  team: string | null
}

interface AuthContextType {
  user: User | null
  loading: boolean
  login: (username: string, password: string) => Promise<void>
  loginWithToken: (token: string, user: User) => void
  register: (data: any) => Promise<void>
  logout: () => Promise<void>
  isAuthenticated: boolean
}

const AuthContext = createContext<AuthContextType | undefined>(undefined)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUserState] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const router = useRouter()

  useEffect(() => {
    const token = getToken()
    const userData = getUser()
    
    if (token && userData) {
      setUserState(userData)
      verifyToken(token)
    }
    setLoading(false)
  }, [])

  const verifyToken = async (token: string) => {
    try {
      const response = await axios.get(`${API_URL}/auth/me`, {
        headers: { Authorization: `Bearer ${token}` }
      })
      setUserState(response.data)
      setUser(response.data)
    } catch (error) {
      sessionStorage.clear()
      setUserState(null)
    }
  }

  const login = async (username: string, password: string) => {
    const formData = new FormData()
    formData.append('username', username)
    formData.append('password', password)

    const response = await axios.post(`${API_URL}/auth/token`, formData)
    const { access_token, user } = response.data
    
    setToken(access_token)
    setUser(user)
    setUserState(user)
    
    router.push('/dashboard')
  }

  const loginWithToken = (token: string, userData: User) => {
    setToken(token)
    setUser(userData)
    setUserState(userData)
    router.push('/dashboard')
  }

  const register = async (data: any) => {
    try {
      // Send registration data
      const response = await axios.post(`${API_URL}/auth/register`, data)
      console.log('Registration successful:', response.data)
      
      // Auto-login after registration
      await login(data.username, data.password)
    } catch (error: any) {
      console.error('Registration error:', error)
      
      if (error.response) {
        // Server responded with error
        throw new Error(error.response.data.detail || 'Registration failed')
      } else if (error.request) {
        // No response from server
        throw new Error('Cannot connect to server. Make sure backend is running on port 8000')
      } else {
        throw new Error('Registration failed. Please try again.')
      }
    }
  }

  const logout = async () => {
    sessionStorage.clear()
    setUserState(null)
    router.push('/login')
  }

  return (
    <AuthContext.Provider value={{
      user,
      loading,
      login,
      loginWithToken,
      register,
      logout,
      isAuthenticated: !!user
    }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return context
}