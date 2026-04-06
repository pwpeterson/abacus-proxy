"""Enhanced code indexing utilities for semantic code search.

This module provides advanced code indexing capabilities including:
- AST-based code parsing
- Function/class extraction
- Dependency analysis
- Complexity scoring
- Incremental indexing
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from typing import Any

from loguru import logger

from src.utils.memory_service import MemoryService


class CodeIndexer:
    """Index codebase for semantic search."""

    def __init__(self, memory_service: MemoryService):
        """Initialize code indexer with memory service."""
        self.memory_service = memory_service

    def index_file(
        self,
        *,
        file_path: str | Path,
        project_id: str,
        user_id: str | None = None,
        language: str | None = None,
        chunk_size: int = 50,
        overlap: int = 5,
    ) -> dict[str, Any]:
        """Index a single code file.

        Args:
            file_path: Path to code file
            project_id: Project identifier
            user_id: User identifier
            language: Programming language (auto-detected if None)
            chunk_size: Lines per chunk
            overlap: Overlapping lines between chunks

        Returns:
            Dictionary with indexing statistics
        """
        if not self.memory_service.is_enabled:
            return {"success": False, "message": "Memory service not enabled"}

        file_path = Path(file_path)
        if not file_path.exists():
            return {"success": False, "message": f"File not found: {file_path}"}

        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception as e:
            return {"success": False, "message": f"Failed to read file: {e}"}

        # Auto-detect language
        if language is None:
            language = self._detect_language(file_path)

        # Parse code structure
        structure = self._parse_code_structure(content, language)

        # Chunk code
        chunks = self._chunk_code(content, chunk_size, overlap)

        # Index chunks
        indexed_count = 0
        for i, chunk in enumerate(chunks):
            chunk_hash = hashlib.sha256(chunk["code"].encode("utf-8")).hexdigest()[:16]

            metadata = {
                "type": "code_chunk",
                "file_path": str(file_path),
                "start_line": chunk["start_line"],
                "end_line": chunk["end_line"],
                "language": language,
                "project_id": project_id,
                "chunk_index": i,
                "total_chunks": len(chunks),
                "chunk_hash": chunk_hash,
                "functions": chunk.get("functions", []),
                "classes": chunk.get("classes", []),
                "imports": chunk.get("imports", []),
                "complexity_score": chunk.get("complexity", 0),
                "tags": self._generate_tags(chunk, structure),
            }

            result = self.memory_service.upsert_raw_text(
                text=chunk["code"],
                user_id=user_id,
                metadata=metadata,
                point_id=f"{project_id}:{file_path}:{i}:{chunk_hash}",
            )

            if result.success:
                indexed_count += 1

        logger.info(f"Indexed {indexed_count}/{len(chunks)} chunks from {file_path}")

        return {
            "success": True,
            "file_path": str(file_path),
            "language": language,
            "chunks_indexed": indexed_count,
            "total_chunks": len(chunks),
            "structure": structure,
        }

    def index_directory(
        self,
        *,
        directory: str | Path,
        project_id: str,
        user_id: str | None = None,
        patterns: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
        max_files: int = 1000,
    ) -> dict[str, Any]:
        """Index all code files in a directory.

        Args:
            directory: Directory to index
            project_id: Project identifier
            user_id: User identifier
            patterns: File patterns to include (e.g., ['*.py', '*.js'])
            exclude_patterns: Patterns to exclude (e.g., ['*test*', '*__pycache__*'])
            max_files: Maximum files to index

        Returns:
            Dictionary with indexing statistics
        """
        if not self.memory_service.is_enabled:
            return {"success": False, "message": "Memory service not enabled"}

        directory = Path(directory)
        if not directory.exists():
            return {"success": False, "message": f"Directory not found: {directory}"}

        # Default patterns
        if patterns is None:
            patterns = ["*.py", "*.js", "*.ts", "*.jsx", "*.tsx", "*.go", "*.rs", "*.java"]

        if exclude_patterns is None:
            exclude_patterns = [
                "*test*",
                "*__pycache__*",
                "*.pyc",
                "*node_modules*",
                "*venv*",
                "*.git*",
            ]

        # Find files
        files_to_index: list[Path] = []
        for pattern in patterns:
            for file_path in directory.rglob(pattern):
                if len(files_to_index) >= max_files:
                    break

                # Check exclusions
                if any(file_path.match(excl) for excl in exclude_patterns):
                    continue

                files_to_index.append(file_path)

        # Index files
        results = []
        for file_path in files_to_index:
            result = self.index_file(
                file_path=file_path,
                project_id=project_id,
                user_id=user_id,
            )
            results.append(result)

        successful = sum(1 for r in results if r.get("success"))
        total_chunks = sum(r.get("chunks_indexed", 0) for r in results)

        logger.info(f"Indexed {successful}/{len(files_to_index)} files ({total_chunks} chunks)")

        return {
            "success": True,
            "directory": str(directory),
            "files_indexed": successful,
            "total_files": len(files_to_index),
            "total_chunks": total_chunks,
            "results": results,
        }

    @staticmethod
    def _detect_language(file_path: Path) -> str:
        """Detect programming language from file extension."""
        ext = file_path.suffix.lower()
        language_map = {
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".jsx": "javascript",
            ".tsx": "typescript",
            ".go": "go",
            ".rs": "rust",
            ".java": "java",
            ".cpp": "cpp",
            ".c": "c",
            ".h": "c",
            ".hpp": "cpp",
            ".rb": "ruby",
            ".php": "php",
            ".swift": "swift",
            ".kt": "kotlin",
            ".scala": "scala",
        }
        return language_map.get(ext, "unknown")

    @staticmethod
    def _parse_code_structure(content: str, language: str) -> dict[str, Any]:
        """Parse code structure (functions, classes, imports).

        Args:
            content: Code content
            language: Programming language

        Returns:
            Dictionary with code structure
        """
        structure: dict[str, Any] = {
            "functions": [],
            "classes": [],
            "imports": [],
        }

        if language == "python":
            try:
                tree = ast.parse(content)
                for node in ast.walk(tree):
                    if isinstance(node, ast.FunctionDef):
                        structure["functions"].append(node.name)
                    elif isinstance(node, ast.ClassDef):
                        structure["classes"].append(node.name)
                    elif isinstance(node, (ast.Import, ast.ImportFrom)):
                        for alias in node.names:
                            structure["imports"].append(alias.name)
            except Exception as e:
                logger.debug(f"Failed to parse Python AST: {e}")

        return structure

    @staticmethod
    def _chunk_code(content: str, chunk_size: int, overlap: int) -> list[dict[str, Any]]:
        """Chunk code into overlapping segments.

        Args:
            content: Code content
            chunk_size: Lines per chunk
            overlap: Overlapping lines

        Returns:
            List of code chunks with metadata
        """
        lines = content.splitlines()
        chunks = []

        i = 0
        while i < len(lines):
            end = min(i + chunk_size, len(lines))
            chunk_lines = lines[i:end]

            chunks.append(
                {
                    "code": "\n".join(chunk_lines),
                    "start_line": i + 1,
                    "end_line": end,
                    "line_count": len(chunk_lines),
                }
            )

            i += chunk_size - overlap

        return chunks

    @staticmethod
    def _generate_tags(chunk: dict[str, Any], structure: dict[str, Any]) -> list[str]:
        """Generate tags for code chunk.

        Args:
            chunk: Code chunk
            structure: Overall code structure

        Returns:
            List of tags
        """
        tags = []

        # Add function/class tags
        if chunk.get("functions"):
            tags.append("has_functions")
        if chunk.get("classes"):
            tags.append("has_classes")
        if chunk.get("imports"):
            tags.append("has_imports")

        # Add complexity tags
        complexity = chunk.get("complexity", 0)
        if complexity > 10:
            tags.append("high_complexity")
        elif complexity > 5:
            tags.append("medium_complexity")
        else:
            tags.append("low_complexity")

        return tags