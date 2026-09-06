import React from 'react';
import {
  NavLink as RouterLink,
  matchPath,
  useLocation,
} from 'react-router-dom';
import ExpandLess from '@mui/icons-material/ExpandLess';
import ExpandMore from '@mui/icons-material/ExpandMore';
import {
  Box,
  Button,
  Collapse,
  List,
  ListItem,
  SxProps,
  Theme,
  Tooltip,
} from '@mui/material';
import { SvgIconComponent } from '@mui/icons-material';

export interface NavItemData {
  href?: string;
  icon?: SvgIconComponent;
  title: string;
  subItems?: NavItemData[];
}

interface NavItemProps extends NavItemData {
  collapsed?: boolean;
  sx?: SxProps<Theme>;
}

function NavItem({
  collapsed = false,
  href,
  icon: Icon,
  title,
  subItems,
  ...rest
}: NavItemProps) {
  const location = useLocation();

  const [open, setOpen] = React.useState(true);
  const active = href
    ? !!matchPath(
        {
          path: href,
          end: true,
        },
        location.pathname,
      )
    : false;

  const buttonContent = (
    <>
      {Icon && <Icon fontSize="small" />}
      {!collapsed && (
        <Box
          component="span"
          sx={{
            flex: 1,
            minWidth: 0,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {title}
        </Box>
      )}
    </>
  );

  const buttonSx = {
    color: 'text.secondary',
    fontWeight: 'medium',
    justifyContent: collapsed ? 'center' : 'flex-start',
    gap: collapsed ? 0 : 1,
    letterSpacing: 0,
    minWidth: 0,
    overflow: 'hidden',
    px: collapsed ? 1 : 1.5,
    textTransform: 'none',
    width: '100%',
    ...(active && {
      color: 'primary.main',
    }),
  };

  if (subItems === undefined) {
    const item = (
      <div>
        <ListItem
          disableGutters
          sx={{
            display: 'flex',
            py: 0,
            ...(rest.sx as object),
          }}
        >
          {href ? (
            <Button
              aria-label={collapsed ? title : undefined}
              component={RouterLink}
              sx={buttonSx}
              to={href}
            >
              {buttonContent}
            </Button>
          ) : (
            <Button disabled sx={buttonSx}>
              {buttonContent}
            </Button>
          )}
        </ListItem>
      </div>
    );

    return collapsed ? (
      <Tooltip title={title} placement="right">
        {item}
      </Tooltip>
    ) : (
      item
    );
  }

  if (collapsed) {
    const item = (
      <div>
        <ListItem
          disableGutters
          sx={{
            display: 'flex',
            py: 0,
            ...(rest.sx as object),
          }}
        >
          <Button
            aria-label={title}
            component={href ? RouterLink : 'button'}
            to={href}
            sx={buttonSx}
          >
            {buttonContent}
          </Button>
        </ListItem>
      </div>
    );

    return (
      <Tooltip title={title} placement="right">
        {item}
      </Tooltip>
    );
  }

  return (
    <div>
      <ListItem
        disableGutters
        sx={{
          display: 'flex',
          py: 0,
        }}
      >
        <Button
          component={href ? RouterLink : 'button'}
          to={href}
          sx={{
            ...buttonSx,
            flex: 1,
          }}
        >
          {buttonContent}
        </Button>
        <Button
          onClick={() => setOpen(!open)}
          sx={{ minWidth: 0, px: 0.5, color: 'text.secondary' }}
        >
          {open ? <ExpandMore /> : <ExpandLess />}
        </Button>
      </ListItem>
      <Collapse in={open} timeout="auto" unmountOnExit>
        <List disablePadding>
          {subItems.map((item) => (
            <NavItem
              collapsed={collapsed}
              href={item.href}
              key={item.title}
              title={item.title}
              icon={item.icon}
              sx={{ pl: 2 }}
            />
          ))}
        </List>
      </Collapse>
    </div>
  );
}

export default NavItem;
