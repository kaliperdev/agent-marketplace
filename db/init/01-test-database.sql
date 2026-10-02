-- The tests' own database. Tests drop and recreate its tables freely, so it
-- must never be the catalog people actually use.
create database catalog_test;
