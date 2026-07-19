push:
	git add .
	git commit -m "new"
	git push origin main

tree:
	tree -a -I '.venv|.venv1|.repos|.git|.terraform|others'

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.log" ! -path "./.git/*" -delete
	find . -type d -name ".ruff_cache" -exec rm -rf {} +
	clear


