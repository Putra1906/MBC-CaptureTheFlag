# Gunakan image Python resmi sebagai base image
FROM python:3.12-slim

# Tetapkan direktori kerja di dalam container
WORKDIR /app

# Salin file requirements.txt ke direktori kerja
COPY requirements.txt .

# Instal dependensi Python
RUN pip install --no-cache-dir -r requirements.txt

# Salin seluruh kode aplikasi ke direktori kerja
COPY . .

# Paparkan port tempat Flask akan berjalan
EXPOSE 5000

# Perintah default untuk menjalankan aplikasi Flask
# Variabel lingkungan sensitif akan diberikan saat runtime melalui `docker run -e ...`
CMD ["flask", "run"]
