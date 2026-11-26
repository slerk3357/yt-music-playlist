CREATE TABLE `decisions` (
	`target` text NOT NULL,
	`video` text NOT NULL,
	`target_name` text,
	`title` text,
	`channel` text,
	`action` text NOT NULL,
	`decided_at` text NOT NULL,
	`by` text,
	`applied_at` text,
	`result` text,
	`error` text,
	PRIMARY KEY(`target`, `video`)
);
