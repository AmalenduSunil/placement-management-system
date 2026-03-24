# MySQL Configuration and Migration Guide

This project now supports MySQL (recommended for production) in addition to the default SQLite database.

## Quick Start

### Development (SQLite - Default)
No additional setup required. The project uses SQLite by default with:
```
DATABASE_URL=sqlite:///placement_db_sqlite3.db
```

### Production (MySQL - Recommended)

#### 1. Install MySQL Server

**Windows:**
- Download from [MySQL Community Server](https://dev.mysql.com/downloads/mysql/)
- Follow the installer wizard

**Linux (Ubuntu/Debian):**
```bash
sudo apt-get update
sudo apt-get install mysql-server
sudo mysql_secure_installation
```

**macOS:**
```bash
brew install mysql
brew services start mysql
mysql_secure_installation
```

#### 2. Create Database and User

Connect to MySQL and run:
```bash
mysql -u root -p
```

Then execute:
```sql
-- Create database with UTF-8 support
CREATE DATABASE placement_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- Create dedicated user
CREATE USER 'placement_user'@'localhost' IDENTIFIED BY 'strong_password_here';

-- Grant privileges
GRANT ALL PRIVILEGES ON placement_db.* TO 'placement_user'@'localhost';
FLUSH PRIVILEGES;

-- For remote connections (if needed)
-- CREATE USER 'placement_user'@'%' IDENTIFIED BY 'strong_password_here';
-- GRANT ALL PRIVILEGES ON placement_db.* TO 'placement_user'@'%';
-- FLUSH PRIVILEGES;
```

#### 3. Install Python Dependencies

```bash
pip install -r requirements.txt
```

This includes `pymysql==1.1.0` for MySQL connections.

#### 4. Configure `.env` File

Copy the `.env.example` template and update the `DATABASE_URL`:

```bash
cp .env.example .env
```

Edit `.env` and set:
```
DATABASE_URL=mysql+pymysql://placement_user:strong_password_here@localhost:3306/placement_db
```

For remote MySQL server:
```
DATABASE_URL=mysql+pymysql://placement_user:strong_password_here@your.db.host:3306/placement_db
```

#### 5. Initialize Database

Run the Flask app - it will automatically create/migrate tables on startup:

```bash
python app.py
```

## Connection String Formats

### Local MySQL
```
mysql+pymysql://username:password@localhost:3306/database_name
```

### Remote MySQL Server
```
mysql+pymysql://username:password@192.168.1.100:3306/database_name
mysql+pymysql://username:password@db.example.com:3306/database_name
```

### With Special Characters in Password
URL-encode special characters:
- `@` → `%40`
- `:` → `%3A`
- `/` → `%2F`

Example:
```
mysql+pymysql://user:pass%40word%3A123@localhost:3306/placement_db
```

## Troubleshooting

### Connection Refused
- Verify MySQL service is running
- Check host/port are correct
- Ensure credentials are valid

### "Access Denied" Error
```bash
# Reset MySQL password if needed
sudo mysql -u root
# Then run:
ALTER USER 'placement_user'@'localhost' IDENTIFIED BY 'new_password';
FLUSH PRIVILEGES;
```

### Database Character Set Issues
Ensure database uses UTF-8:
```sql
ALTER DATABASE placement_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

### Connection Timeout
Adjust connection pooling in `config.py`:
```python
SQLALCHEMY_ENGINE_OPTIONS = {
    "pool_pre_ping": True,
    "pool_recycle": 3600,
    "pool_size": 20,        # Increase for more concurrent connections
    "max_overflow": 40,
    "connect_args": {
        "connect_timeout": 10,  # Increase timeout in seconds
    }
}
```

## Performance Tips

1. **Index frequently queried columns:**
   ```sql
   CREATE INDEX idx_student_email ON students(email);
   CREATE INDEX idx_placement_cmpname ON placement(cmpname);
   ```

2. **Enable query logging for debugging:**
   Set in `.env`: `SQLALCHEMY_ECHO=1`

3. **Monitor connections:**
   ```sql
   SHOW PROCESSLIST;
   SHOW STATUS LIKE 'Threads%';
   ```

4. **Regular backups:**
   ```bash
   mysqldump -u placement_user -p placement_db > backup_$(date +%Y%m%d_%H%M%S).sql
   ```

## Migrating from SQLite to MySQL

1. **Export SQLite data:**
   ```bash
   sqlite3 placement_db_sqlite3.db .dump > sqlite_dump.sql
   ```

2. **Create MySQL database** (as shown in step 2 above)

3. **Update `.env`** with MySQL connection string

4. **Restart Flask app** - it will create new tables in MySQL and you can migrate data manually or using a migration script

## Additional Resources

- [SQLAlchemy MySQL Documentation](https://docs.sqlalchemy.org/en/20/dialects/mysql.html)
- [PyMySQL Documentation](https://pymysql.readthedocs.io/)
- [MySQL Official Documentation](https://dev.mysql.com/doc/)
