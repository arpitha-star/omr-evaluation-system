/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        primary: {
          dark: '#1e3a5f',
          DEFAULT: '#2563eb',
          light: '#3b82f6',
        },
        secondary: {
          dark: '#c2410c',
          DEFAULT: '#f97316',
          light: '#fb923c',
        },
        dark: {
          DEFAULT: '#0f172a',
          light: '#1e293b',
        }
      },
    },
  },
  plugins: [],
}

